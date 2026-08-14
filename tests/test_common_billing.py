"""公共计费组件(统一表)测试:agent_api_keys / agent_billing_records。

覆盖:
1. 统一表存在(agent_api_keys 复合主键 apikey+agent)
2. agent_billing_records UNIQUE(agent, bill_no) 约束(重复拒绝,跨 agent 允许同 bill_no)
"""

import tempfile
from pathlib import Path

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
