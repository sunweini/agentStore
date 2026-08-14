"""管理控制台 API 测试(超级管理员,SQLite 后端)。"""

import pytest
from fastapi.testclient import TestClient

from common import db
from common.admin_api import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("ADMIN_APIKEY", "sk-super")
    db.init_tables()
    return TestClient(app)


def _auth():
    return {"Authorization": "Bearer sk-super"}


def test_auth_required(client):
    r = client.get("/api/v1/admin/agents")
    assert r.status_code == 403
    r = client.get("/api/v1/admin/agents", headers=_auth())
    assert r.status_code == 200


def test_create_and_list_key(client):
    r = client.post("/api/v1/admin/apikeys", json={
        "agent": "sentiment", "role": "admin", "free_quota": 50, "paid_quota": 7}, headers=_auth())
    assert r.status_code == 200
    apikey = r.json()["apikey"]
    assert apikey.startswith("sk-")
    r = client.get("/api/v1/admin/apikeys", headers=_auth())
    row = next(x for x in r.json()["keys"] if x["apikey"] == apikey)
    assert row["role"] == "admin" and row["free"]["total"] == 50 and row["paid"]["total"] == 7
    # 负额度 → 400
    r = client.post("/api/v1/admin/apikeys", json={"agent": "sentiment", "free_quota": -1},
                    headers=_auth())
    assert r.status_code == 400


def test_set_role_and_update_and_delete(client):
    k = client.post("/api/v1/admin/apikeys", json={"agent": "sentiment"}, headers=_auth()).json()["apikey"]
    # 换 key(须在改角色前:update_apikey 对 admin key 有 403 守卫,design §8)
    r = client.put("/api/v1/admin/apikeys", json={"apikey": k, "agent": "sentiment", "new_apikey": "sk-new012"},
                   headers=_auth())
    assert r.status_code == 200 and r.json()["new_apikey"] == "sk-new012"
    # 改角色
    r = client.patch("/api/v1/admin/apikeys", json={"apikey": "sk-new012", "agent": "sentiment", "role": "admin"},
                     headers=_auth())
    assert r.status_code == 200 and r.json()["role"] == "admin"
    # 软删(httpx TestClient.delete 不接受 json kwarg,用 request("DELETE") 带 body)
    r = client.request("DELETE", "/api/v1/admin/apikeys",
                       json={"apikey": "sk-new012", "agent": "sentiment"}, headers=_auth())
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert client.get("/api/v1/admin/apikeys", headers=_auth()).json()["keys"][0]["status"] == "deleted"


def test_quota_add(client):
    k = client.post("/api/v1/admin/apikeys", json={"agent": "sentiment"}, headers=_auth()).json()["apikey"]
    r = client.post("/api/v1/admin/apikeys/quota", json={"apikey": k, "agent": "sentiment",
                                                         "type": "paid", "count": 10}, headers=_auth())
    assert r.status_code == 200
    row = next(x for x in client.get("/api/v1/admin/apikeys", headers=_auth()).json()["keys"]
               if x["apikey"] == k)
    assert row["paid"]["total"] == 10
    r = client.post("/api/v1/admin/apikeys/quota", json={"apikey": k, "agent": "sentiment",
                                                         "type": "free", "count": 0}, headers=_auth())
    assert r.status_code == 400  # count 必须为正
    r = client.post("/api/v1/admin/apikeys/quota", json={"apikey": k, "agent": "sentiment",
                                                         "type": "premium", "count": 5}, headers=_auth())
    assert r.status_code == 400  # type 非法


def test_set_role_invalid_role_and_wrong_token(client):
    k = client.post("/api/v1/admin/apikeys", json={"agent": "sentiment"}, headers=_auth()).json()["apikey"]
    # 错误 token → 403
    r = client.get("/api/v1/admin/agents", headers={"Authorization": "Bearer sk-wrong"})
    assert r.status_code == 403
    # 非法 role → 400(set_role 校验)
    r = client.patch("/api/v1/admin/apikeys", json={"apikey": k, "agent": "sentiment", "role": "bogus"},
                     headers=_auth())
    assert r.status_code == 400


def test_create_apikey_empty_agent_422(client):
    # 空 agent → pydantic min_length=1 → 422
    r = client.post("/api/v1/admin/apikeys", json={"agent": ""}, headers=_auth())
    assert r.status_code == 422


def test_report_endpoints(client):
    client.post("/api/v1/admin/apikeys", json={"agent": "sentiment", "free_quota": 5}, headers=_auth())
    r = client.get("/api/v1/admin/report/summary", headers=_auth())
    assert r.status_code == 200 and r.json()["total"]["key_count"] == 1
    r = client.get("/api/v1/admin/report/history", headers=_auth())
    assert r.status_code == 200 and isinstance(r.json()["series"], list)
    r = client.get("/api/v1/admin/report/history?days=0", headers=_auth())
    assert r.status_code == 400  # days 越界
    r = client.get("/api/v1/admin/agents", headers=_auth())
    assert r.status_code == 200 and any(a["agent"] == "sentiment" for a in r.json()["agents"])


def test_index_serves_admin_html(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
