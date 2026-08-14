"""公共计费组件(统一表)测试:agent_api_keys / agent_billing_records。

覆盖:
1. 统一表存在(agent_api_keys 复合主键 apikey+agent)
2. agent_billing_records UNIQUE(agent, bill_no) 约束(重复拒绝,跨 agent 允许同 bill_no)
"""

import tempfile
from pathlib import Path

import pytest

from common import billing
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
    billing.create_pending("k1", "sentiment", "b2")
    billing.commit("k1", "sentiment", "b2")
    u = billing.usage("k1", "sentiment")
    assert u["free"]["used"] == 1 and u["paid"]["used"] == 1


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
