"""sentiment-query-agent(舆情方案生成 Agent)测试。

覆盖:
1. skill 分步脚本单测(格式契约/缺字段记 GAP)
2. store 单测(勾选汇总/转换/文件库)
3. 鉴权/计费单测(apikey 校验/归属 403/pending→commit)

图单测/端到端(真实 MCP+LLM)依赖外部服务,单独跑。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agents.sentiment_query_agent.graph.nodes import _extract_json
from agents.sentiment_query_agent.store import converter, scheme_store
from common import apikey_mgmt, auth, billing, db

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_data_paths():
    """运行时数据目录必须落在项目根 data/(防路径层级错)。"""
    assert str(scheme_store._DATA_DIR).endswith(f"{_PROJECT_ROOT}/data/schemes")

_SCRIPTS = (
    Path(__file__).resolve().parent.parent
    / "agents" / "sentiment_query_agent" / "skills" / "overseas-sentiment-query-builder" / "scripts"
)


def _run_script(name: str, payload: dict) -> dict:
    """调 skill 分步脚本,返回 stdout JSON(失败抛 RuntimeError)。"""
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / name)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{name} 失败: {proc.stderr}")
    return json.loads(proc.stdout)


# ===== 1. skill 分步脚本单测 =====


def test_step1_entities_format():
    out = _run_script("step1_entities.py", {
        "entities": {"parent": "中国十五冶",
                     "overseas_entities": [{"name": "NFCA", "lang": "en", "region": "赞比亚"}]},
    })
    assert out["entities"]["parent"] == "中国十五冶"
    assert out["entities"]["overseas_entities"][0]["name"] == "NFCA"
    # 缺字段记 GAP
    assert any("GAP" in g for g in out.get("_gaps", []))


def test_step4_queries_tracks():
    """轨 key 用中文语义名:全量新闻/负面新闻/行业新闻/快讯/司法/招标。"""
    out = _run_script("step4_queries.py", {"schemes": [
        {"id": "Q0", "name": "集团层", "tracks": [
            {"key": "全量新闻", "boolean": "(A)", "google": "(A)"},
            {"key": "负面新闻", "boolean": "(A) AND (strike)", "google": "(A) strike"},
        ]},
    ]})
    sc = out["schemes"][0]
    assert sc["tracks"][0]["boolean_query"] == "(A)"
    assert sc["tracks"][1]["key"] == "负面新闻"
    # 每轨默认 selected=True,sources 空列表,且不含 risk 字段
    assert sc["tracks"][0]["selected"] is True
    assert sc["tracks"][0]["sources"] == []
    assert "risk" not in sc["tracks"][0]


def test_step4_rejects_old_letter_keys():
    """旧字母轨 key(a/b/c)不在新 TRACK_KEYS:全部无效 → 脚本非 0 退出。"""
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "step4_queries.py")],
        input=json.dumps({"schemes": [{"id": "Q0", "name": "集团层", "tracks": [
            {"key": "a", "boolean": "(A)", "google": "(A)"},
        ]}]}),
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0
    assert "FORMAT_ERROR" in proc.stderr


def test_step6_cadence_fix_fast():
    """快讯轨强制快讯/小时级;risk 字段保留(修复后行为)。"""
    out = _run_script("step6_cadence.py", {"schemes": [
        {"id": "Q3", "tracks": [{"key": "快讯", "frequency": "周级", "risk": "critical"}]},
    ]})
    tr = out["schemes"][0]["tracks"][0]
    assert tr["frequency"] == "快讯/小时级"
    assert tr["risk"] == "critical"  # risk 保留
    assert any("GAP" in g for g in out.get("_gaps", []))


def test_script_bad_json_fails():
    """LLM 输出非 JSON → 脚本非 0 退出。"""
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "step1_entities.py")],
        input="not json", capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0
    assert "FORMAT_ERROR" in proc.stderr


# ===== 2. store 单测 =====


def test_extract_json_robust():
    """LLM 输出容错解析:纯 JSON/代码块/带说明文字。"""
    assert _extract_json('{"a": 1}') == {"a": 1}
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('好的:\n{"a": 1}\n完成') == {"a": 1}
    assert _extract_json('不是 JSON') is None
    assert _extract_json('') is None


def _sample_group() -> dict:
    return {
        "group_id": "g1", "owner": "user1", "company_name": "测试公司",
        "status": "review", "schemes": [
            {"id": "Q0", "name": "集团层", "region": "全语种", "lang": "中",
             "desc": "", "gaps": ["GAP001 拼写待证"], "selected": True,
             "tracks": [
                 {"key": "全量新闻", "boolean_query": "(A)", "google_query": "(A)",
                  "sources": ["media.com"], "frequency": "周级",
                  "relevance": "direct", "selected": True},
                 {"key": "负面新闻", "boolean_query": "(A) AND (strike)", "google_query": "(A) strike",
                  "sources": [], "frequency": "日级",
                  "relevance": "direct", "selected": False},
             ]},
        ],
        "keywords": [{"layer": "A", "category": "A1", "terms": "\"测试\"", "lang": "全",
                      "guard": "", "note": ""}],
    }


def test_converter_selected_only():
    """转换层:只导出勾选的轨(方案 selected 且轨 selected)。"""
    spec = converter.group_to_spec(_sample_group())
    assert len(spec["tasks"]) == 1  # 只有 全量新闻 轨(负面新闻 未选)
    assert spec["tasks"][0]["boolean"] == "(A)"
    assert spec["tasks"][0]["sources"] == ["media.com"]
    assert "risk" not in spec["tasks"][0]
    assert spec["keywords"][0]["layer"] == "A"
    # GAP → extra_notes
    assert any(n["key"].startswith("GAP") for n in spec["extra_notes"])


def test_store_save_load_roundtrip(tmp_path, monkeypatch):
    """文件库:草稿/正式/索引读写。"""
    monkeypatch.setattr(scheme_store, "_DATA_DIR", tmp_path)
    group = _sample_group()
    scheme_store.save_draft(group)
    assert (tmp_path / "g1.draft.json").exists()
    loaded = scheme_store.load_group("g1")
    assert loaded["company_name"] == "测试公司"

    group["status"] = "committed"
    scheme_store.save_committed(group)
    assert (tmp_path / "g1.json").exists()
    assert not (tmp_path / "g1.draft.json").exists()  # 草稿删除
    assert scheme_store.load_group("g1")["status"] == "committed"
    # 索引按 owner 过滤
    assert [g["group_id"] for g in scheme_store.list_groups("user1")] == ["g1"]
    assert scheme_store.list_groups("other") == []


# ===== 3. 鉴权/配额/计费单测(SQLite 后端,公共组件 common,agent='sentiment') =====


@pytest.fixture()
def sqlite_db(monkeypatch, tmp_path):
    """测试用临时文件 SQLite(内存库每个连接独立,建表后新连接看不到)。"""
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ADMIN_APIKEY", "sk-admintest123")
    from common import db
    db.init_tables()
    return db


def _seed_apikey(apikey="sk-usertest123", free=10, paid=0, role="normal"):
    from common import db
    db.execute(
        "INSERT INTO agent_api_keys (apikey, agent, role, status, free_quota, paid_quota) "
        "VALUES (%s, 'sentiment', %s, 'active', %s, %s)",
        (apikey, role, free, paid),
    )


def test_auth_assert_owner(sqlite_db):
    """归属校验(common.auth,agent='sentiment'):owner 不符 → 403;本 agent 管理员放行。"""
    _seed_apikey("sk-usertest123")
    _seed_apikey("sk-admintest123", role="admin", free=99999999)
    with pytest.raises(Exception):
        auth.assert_owner("sk-usertest123", "other", "sentiment")
    auth.assert_owner("sk-usertest123", "sk-usertest123", "sentiment")  # 本人不抛
    # 管理员放行(per-agent:sentiment 管理员)
    auth.assert_owner("sk-admintest123", "other", "sentiment", admin="sk-admintest123")


def test_billing_pending_commit(sqlite_db):
    """计费(common,agent='sentiment'):创建记 pending,commit 转正式 + 扣免费额度。"""
    _seed_apikey()
    billing.create_pending("sk-usertest123", "sentiment", "g1")
    rows = db.query("SELECT status FROM agent_billing_records WHERE agent='sentiment' AND bill_no='g1'")
    assert rows[0]["status"] == "pending"
    billing.commit("sk-usertest123", "sentiment", "g1")
    rows = db.query("SELECT status, quota_type FROM agent_billing_records WHERE agent='sentiment' AND bill_no='g1'")
    assert rows[0]["status"] == "committed"
    assert rows[0]["quota_type"] == "free"
    key = db.query("SELECT free_used FROM agent_api_keys WHERE apikey='sk-usertest123' AND agent='sentiment'")[0]
    assert key["free_used"] == 1  # 免费扣 1


def test_billing_max_pending(sqlite_db):
    """防刷:超过并发上限拒绝。"""
    _seed_apikey()
    for i in range(5):
        billing.create_pending("sk-usertest123", "sentiment", f"g{i}")
    with pytest.raises(Exception):
        billing.create_pending("sk-usertest123", "sentiment", "g_over")


def test_billing_cancel_pending_releases_quota(sqlite_db):
    """stop 任务:取消 pending 释放并发额度,不扣额度。"""
    _seed_apikey()
    for i in range(3):
        billing.create_pending("sk-usertest123", "sentiment", f"g{i}")
    billing.cancel_pending("sk-usertest123", "sentiment", "g1")
    rows = db.query(
        "SELECT bill_no FROM agent_billing_records WHERE apikey='sk-usertest123' "
        "AND agent='sentiment' AND status='pending'")
    assert [r["bill_no"] for r in rows] == ["g0", "g2"]
    key = db.query("SELECT free_used FROM agent_api_keys WHERE apikey='sk-usertest123' AND agent='sentiment'")[0]
    assert key["free_used"] == 0  # 不扣额度


def test_quota_deduction_order(sqlite_db):
    """额度扣减:先免费后付费。"""
    _seed_apikey(free=2, paid=3)
    for i in range(5):
        billing.create_pending("sk-usertest123", "sentiment", f"g{i}")
    for i in range(5):
        billing.commit("sk-usertest123", "sentiment", f"g{i}")
    key = db.query("SELECT free_used, paid_used FROM agent_api_keys "
                   "WHERE apikey='sk-usertest123' AND agent='sentiment'")[0]
    assert key["free_used"] == 2  # 免费先用完
    assert key["paid_used"] == 3  # 再扣付费


def test_quota_insufficient_rejected(sqlite_db):
    """额度不足:check_quota 拒绝。"""
    _seed_apikey(free=1, paid=0)
    billing.check_quota("sk-usertest123", "sentiment")  # 还有 1 次,通过
    billing.create_pending("sk-usertest123", "sentiment", "g1")
    billing.commit("sk-usertest123", "sentiment", "g1")  # 用掉唯一额度
    with pytest.raises(Exception):
        billing.check_quota("sk-usertest123", "sentiment")  # 0 剩余,拒绝


def test_apikey_crud(sqlite_db):
    """apikey 管理(common.apikey_mgmt,agent='sentiment'):创建(默认 10/0)/换 key(资费继承)/停用(软删)。"""
    from common.apikey_mgmt import create_apikey, deactivate_apikey, update_apikey
    # 创建(公共版服务端随机 key,默认 10/0)
    r = create_apikey("sentiment", "user1")
    key = r["apikey"]
    assert r["free_quota"] == 10 and r["paid_quota"] == 0
    # 换 key:旧→新,资费继承
    update_apikey("sentiment", key, "sk-renamed123")
    row = db.query("SELECT apikey, free_quota FROM agent_api_keys "
                   "WHERE apikey='sk-renamed123' AND agent='sentiment'")[0]
    assert row["free_quota"] == 10  # 资费继承
    assert not db.query("SELECT apikey FROM agent_api_keys WHERE apikey=%s AND agent='sentiment'", (key,))  # 旧 key 没了
    # 停用:软删(公共版需管理员授权;admin 目标可停用,见 common 契约)
    admin = create_apikey("sentiment", "admin1", role="admin")["apikey"]
    deactivate_apikey("sentiment", "sk-renamed123", admin)
    row = db.query("SELECT status FROM agent_api_keys WHERE apikey='sk-renamed123' AND agent='sentiment'")[0]
    assert row["status"] == "deleted"


def test_admin_usage_all(sqlite_db):
    """管理员查全部额度(agent='sentiment')。"""
    _seed_apikey("sk-a", free=10, paid=0)
    _seed_apikey("sk-b", free=10, paid=5)
    users = billing.usage_all(agent="sentiment")
    assert len(users) == 2
    by_key = {u["apikey"]: u for u in users}
    assert by_key["sk-b"]["paid"]["total"] == 5


def test_add_quota(sqlite_db):
    """管理员加额度。"""
    _seed_apikey()
    billing.add_free_quota("sk-usertest123", "sentiment", 5)
    billing.add_paid_quota("sk-usertest123", "sentiment", 3)
    row = db.query("SELECT free_quota, paid_quota FROM agent_api_keys "
                   "WHERE apikey='sk-usertest123' AND agent='sentiment'")[0]
    assert row["free_quota"] == 15 and row["paid_quota"] == 3


# ===== 4. 全局账单接口 GET /api/v1/billing/usage_all(公共组件 Task 8) =====
# TestClient 走 FastAPI 全链路(sqlite_db fixture 建表 + ADMIN_APIKEY=sk-admintest123)。
# 启动事件 ensure_admin('sentiment') 把 sk-admintest123 铸为 sentiment 管理员(幂等)。

from fastapi.testclient import TestClient  # noqa: E402
from agents.sentiment_query_agent.api import app as sentiment_app  # noqa: E402


def _seed_apikey_for_agent(apikey, agent, free=10, paid=0, role="normal"):
    """跨 agent 造普通用户行(全局账单接口验证多 agent 场景)。"""
    db.execute(
        "INSERT INTO agent_api_keys (apikey, agent, role, status, free_quota, paid_quota) "
        "VALUES (%s, %s, %s, 'active', %s, %s)",
        (apikey, agent, role, free, paid),
    )


def test_usage_all_endpoint_admin_sees_all_agents(sqlite_db):
    """全局账单:管理员 200,返回所有 agent 的普通用户账单(含 agent 维度)。"""
    apikey_mgmt.ensure_admin("sentiment")  # sk-admintest123(ADMIN_APIKEY)→ sentiment 管理员
    _seed_apikey_for_agent("sk-senti-user", "sentiment", free=10, paid=0)
    _seed_apikey_for_agent("sk-contract-user", "contract", free=20, paid=5)
    with TestClient(sentiment_app) as c:
        r = c.get("/api/v1/billing/usage_all",
                  headers={"Authorization": "Bearer sk-admintest123"})
    assert r.status_code == 200
    # 顺序确定(ORDER BY agent, apikey):contract < sentiment
    assert [u["agent"] for u in r.json()["usage"]] == ["contract", "sentiment"]
    by_key = {u["apikey"]: u for u in r.json()["usage"]}
    assert by_key["sk-contract-user"]["paid"]["total"] == 5


def test_usage_all_endpoint_admin_agent_filter(sqlite_db):
    """agent 过滤:指定 agent 只返回该 agent 账单。"""
    apikey_mgmt.ensure_admin("sentiment")
    _seed_apikey_for_agent("sk-senti-user", "sentiment", free=10, paid=0)
    _seed_apikey_for_agent("sk-contract-user", "contract", free=20, paid=5)
    with TestClient(sentiment_app) as c:
        r = c.get("/api/v1/billing/usage_all", params={"agent": "contract"},
                  headers={"Authorization": "Bearer sk-admintest123"})
    assert r.status_code == 200
    assert [u["apikey"] for u in r.json()["usage"]] == ["sk-contract-user"]
    assert [u["agent"] for u in r.json()["usage"]] == ["contract"]


def test_usage_all_endpoint_non_admin_403(sqlite_db):
    """非管理员 → 403。"""
    _seed_apikey()  # sk-usertest123,normal
    with TestClient(sentiment_app) as c:
        r = c.get("/api/v1/billing/usage_all",
                  headers={"Authorization": "Bearer sk-usertest123"})
    assert r.status_code == 403
