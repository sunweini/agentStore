"""公共计费组件(统一表)测试:agent_api_keys / agent_billing_records。

覆盖:
1. 统一表存在(agent_api_keys 复合主键 apikey+agent)
2. agent_billing_records UNIQUE(agent, bill_no) 约束(重复拒绝,跨 agent 允许同 bill_no)
"""

import tempfile
from pathlib import Path

import pytest

from common import billing
from common import apikey_mgmt as billing_mgmt
from common import db


def _sqlite_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "t.db"))
    db.init_tables()


def test_agent_tables_exist(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    tables = {r["name"] for r in db.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'agent_%'")}
    assert {"agent_api_keys", "agent_billing_records"} <= tables


def test_agent_billing_unique_agent_billno(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no) VALUES (%s,%s,%s)",
               ("k", "sentiment", "b1"))
    try:
        db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no) VALUES (%s,%s,%s)",
                   ("k", "sentiment", "b1"))
        assert False, "应拒绝重复 (agent, bill_no)"
    except Exception:
        pass
    # 不同 agent 允许同 bill_no
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no) VALUES (%s,%s,%s)",
               ("k", "contract", "b1"))


def _seed(apikey="k1", agent="sentiment", free=10, paid=0):
    db.execute(
        "INSERT INTO agent_api_keys (apikey, agent, free_quota, paid_quota) "
        "VALUES (%s,%s,%s,%s)", (apikey, agent, free, paid))


def test_check_quota_ok_and_403(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed()
    billing.check_quota("k1", "sentiment")  # 有行且额度足够 → 不抛
    with pytest.raises(Exception) as e:
        billing.check_quota("k1", "contract")  # 无行 → _active_apikey 抛 401
    assert getattr(e.value, "status_code", None) == 401
    db.execute("UPDATE agent_api_keys SET free_quota=0, free_used=0, "
               "paid_quota=0, paid_used=0 WHERE apikey='k1' AND agent='sentiment'")
    with pytest.raises(Exception) as e:
        billing.check_quota("k1", "sentiment")  # 额度耗尽 → 403
    assert getattr(e.value, "status_code", None) == 403


def test_create_pending_limit_429(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed()
    for i in range(5):
        billing.create_pending("k1", "sentiment", f"b{i}")
    with pytest.raises(Exception) as e:
        billing.create_pending("k1", "sentiment", "b6")
    assert getattr(e.value, "status_code", None) == 429


def test_commit_free_then_paid(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed(free=1, paid=1)
    billing.create_pending("k1", "sentiment", "b1")
    billing.commit("k1", "sentiment", "b1")
    u = billing.usage("k1", "sentiment")
    assert u["free"]["used"] == 1 and u["paid"]["used"] == 0
    assert db.query("SELECT quota_type FROM agent_billing_records "
                    "WHERE agent=%s AND bill_no=%s", ("sentiment", "b1"))[0]["quota_type"] == "free"
    billing.create_pending("k1", "sentiment", "b2")
    billing.commit("k1", "sentiment", "b2")
    u = billing.usage("k1", "sentiment")
    assert u["free"]["used"] == 1 and u["paid"]["used"] == 1
    assert db.query("SELECT quota_type FROM agent_billing_records "
                    "WHERE agent=%s AND bill_no=%s", ("sentiment", "b2"))[0]["quota_type"] == "paid"


def test_commit_no_pending_404(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed()
    with pytest.raises(Exception) as e:
        billing.commit("k1", "sentiment", "b-none")  # 未 create_pending → 404
    assert getattr(e.value, "status_code", None) == 404


def test_cancel_filters_apikey(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed()
    billing.create_pending("k1", "sentiment", "b1")
    billing.cancel_pending("k1", "sentiment", "b1")
    assert billing.usage("k1", "sentiment")["pending_count"] == 0


def test_usage_all_filters_agent(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed("k1", "sentiment")
    _seed("k2", "contract")
    assert {u["agent"] for u in billing.usage_all()} >= {"sentiment", "contract"}
    assert [u["agent"] for u in billing.usage_all(agent="sentiment")] == ["sentiment"]


def test_create_apikey_roles(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    k = billing_mgmt.create_apikey("contract", "tester")
    assert k["apikey"].startswith("sk-")
    with pytest.raises(ValueError):
        billing_mgmt.create_apikey("contract", "x", role="superadmin")


def test_ensure_admin_idempotent(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    billing_mgmt.ensure_admin("sentiment")
    billing_mgmt.ensure_admin("sentiment")
    rows = db.query("SELECT * FROM agent_api_keys WHERE role='admin' AND agent='sentiment'")
    assert len(rows) == 1 and rows[0]["free_quota"] == 99999999


def test_deactivate_rule(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    admin = billing_mgmt.create_apikey("sentiment", "admin", role="admin")["apikey"]
    other_admin = billing_mgmt.create_apikey("sentiment", "a2", role="admin")["apikey"]
    user = billing_mgmt.create_apikey("sentiment", "u")["apikey"]
    billing_mgmt.deactivate_apikey("sentiment", user, admin)      # admin 停用普通用户 OK
    billing_mgmt.deactivate_apikey("sentiment", other_admin, admin)  # admin 目标可停用
    with pytest.raises(Exception) as e:
        billing_mgmt.deactivate_apikey("sentiment", admin, admin)  # 不可停用自己
    assert getattr(e.value, "status_code", None) == 403


def test_update_apikey_migrates(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed("k1", "sentiment", free=5, paid=3)
    db.execute("UPDATE agent_api_keys SET free_used=1, paid_used=2 "
               "WHERE apikey=%s AND agent=%s", ("k1", "sentiment"))
    billing.create_pending("k1", "sentiment", "b1")  # pending 流水,验证 apikey 重写
    r = billing_mgmt.update_apikey("sentiment", "k1", "k2")
    assert r["new_apikey"] == "k2" and r["migrated"] is True
    # 旧 key 失效(行已不存在 → _active_apikey 抛 401)
    with pytest.raises(Exception) as e:
        billing.usage("k1", "sentiment")
    assert getattr(e.value, "status_code", None) == 401
    # 新 key 额度/role 继承(free/paid 四元组一致,role 不变)
    u = billing.usage("k2", "sentiment")
    assert u["free"] == {"total": 5, "used": 1, "remaining": 4}
    assert u["paid"] == {"total": 3, "used": 2, "remaining": 1}
    assert u["role"] == "normal"
    # 流水 apikey 已重写为 new
    rec = db.query("SELECT apikey FROM agent_billing_records "
                   "WHERE agent=%s AND bill_no=%s", ("sentiment", "b1"))
    assert rec[0]["apikey"] == "k2"


def test_update_apikey_atomic(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed("k1", "sentiment")
    billing.create_pending("k1", "sentiment", "b1")
    # 强制流水 UPDATE 失败(SQLite 触发器 RAISE)→ 事务应整体回滚,不留半更新
    db.execute("CREATE TRIGGER trg_fail_update BEFORE UPDATE ON agent_billing_records "
               "BEGIN SELECT RAISE(ABORT, 'forced'); END")
    try:
        with pytest.raises(Exception):
            billing_mgmt.update_apikey("sentiment", "k1", "k2")
    finally:
        db.execute("DROP TRIGGER trg_fail_update")
    # 主键未改:old key 仍有效、new key 不存在、流水 apikey 仍是 k1
    assert billing.usage("k1", "sentiment")["free"]["total"] == 10
    assert db.query("SELECT apikey FROM agent_api_keys WHERE agent=%s",
                    ("sentiment",))[0]["apikey"] == "k1"
    assert db.query("SELECT apikey FROM agent_billing_records "
                    "WHERE agent=%s AND bill_no=%s", ("sentiment", "b1"))[0]["apikey"] == "k1"


def test_ensure_admin_rebuilds_after_deactivate(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    # 无 ADMIN_APIKEY → 自动生成首个管理员
    billing_mgmt.ensure_admin("sentiment")
    assert len(db.query("SELECT apikey FROM agent_api_keys "
                        "WHERE agent='sentiment' AND role='admin' AND status='active'")) == 1
    # 模拟管理员被软删(清理/误删;deactivate_apikey 不可停用自己,故直接 UPDATE)
    db.execute("UPDATE agent_api_keys SET status='deleted' "
               "WHERE agent='sentiment' AND role='admin' AND status='active'")
    # 幂等守卫应忽略已软删的 admin → 重建出新的 active admin
    billing_mgmt.ensure_admin("sentiment")
    admins = db.query("SELECT apikey, status FROM agent_api_keys "
                      "WHERE agent='sentiment' AND role='admin'")
    assert [a["status"] for a in admins] == ["deleted", "active"]
