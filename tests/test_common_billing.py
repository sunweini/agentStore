"""公共计费组件(统一表)测试:agent_api_keys / agent_billing_records。

覆盖:
1. 统一表存在(agent_api_keys 复合主键 apikey+agent)
2. agent_billing_records UNIQUE(agent, bill_no) 约束(重复拒绝,跨 agent 允许同 bill_no)
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from common import auth
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


def test_commit_wrong_apikey_not_cross_charge(tmp_path, monkeypatch):
    """M6:错误 apikey 配有效 (agent, bill_no) 不扣他人/不误标(commit 按 apikey 过滤)。

    场景:记录 b1 属于 k1;用 k2(同 agent 有效 key)commit b1 → 404,且
    k1 的额度不扣、b1 保持 pending。
    """
    _sqlite_env(tmp_path, monkeypatch)
    _seed("k1", "sentiment", free=5, paid=0)
    _seed("k2", "sentiment", free=5, paid=0)
    billing.create_pending("k1", "sentiment", "b1")
    with pytest.raises(Exception) as e:
        billing.commit("k2", "sentiment", "b1")  # 非记录 owner 的 apikey → 404
    assert getattr(e.value, "status_code", None) == 404
    # k1 额度未扣、b1 仍 pending、k2 未产生任何扣费
    u1 = billing.usage("k1", "sentiment")
    assert u1["free"]["used"] == 0
    assert db.query("SELECT status FROM agent_billing_records "
                    "WHERE agent=%s AND bill_no=%s", ("sentiment", "b1"))[0]["status"] == "pending"
    u2 = billing.usage("k2", "sentiment")
    assert u2["free"]["used"] == 0 and u2["pending_count"] == 0


def test_cancel_filters_apikey(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed()
    billing.create_pending("k1", "sentiment", "b1")
    billing.cancel_pending("k1", "sentiment", "b1")
    assert billing.usage("k1", "sentiment")["pending_count"] == 0


def test_commit_both_exhausted_403_no_overdraw(tmp_path, monkeypatch):
    """M7:free+paid 都耗尽后 commit → 403,不超扣(事务回滚)。

    场景:free_quota=1/paid_quota=0。commit b1 用掉唯一免费额度后,双耗尽;
    此时仍可 create_pending(模拟并发/额度被调减后残留的 pending),再 commit
    应 403,paid_used 保持 0(不超扣),b2 记录不落 committed(事务回滚)。
    """
    _sqlite_env(tmp_path, monkeypatch)
    _seed(free=1, paid=0)
    billing.create_pending("k1", "sentiment", "b1")
    billing.commit("k1", "sentiment", "b1")  # 用掉唯一免费额度 → free_used=1
    billing.create_pending("k1", "sentiment", "b2")  # 额度耗尽后残留的 pending
    with pytest.raises(Exception) as e:
        billing.commit("k1", "sentiment", "b2")  # 双耗尽 → 403
    assert getattr(e.value, "status_code", None) == 403
    u = billing.usage("k1", "sentiment")
    assert u["free"]["used"] == 1 and u["paid"]["used"] == 0  # 不超扣
    # 事务回滚:该记录未被误标 committed,仍是 pending
    assert db.query("SELECT status FROM agent_billing_records "
                    "WHERE agent=%s AND bill_no=%s", ("sentiment", "b2"))[0]["status"] == "pending"


def test_usage_all_filters_agent(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed("k1", "sentiment")
    _seed("k2", "contract")
    assert {u["agent"] for u in billing.usage_all()} >= {"sentiment", "contract"}
    assert [u["agent"] for u in billing.usage_all(agent="sentiment")] == ["sentiment"]


def test_usage_all_sorted(tmp_path, monkeypatch):
    """M5:usage_all 返回顺序确定(ORDER BY agent, apikey)。"""
    _sqlite_env(tmp_path, monkeypatch)
    _seed("k2", "sentiment")
    _seed("k1", "sentiment")
    _seed("a1", "contract")
    rows = billing.usage_all()
    assert [(u["agent"], u["apikey"]) for u in rows] == [
        ("contract", "a1"), ("sentiment", "k1"), ("sentiment", "k2"),
    ]


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


def test_ensure_admin_auto_gen_logs_masked_apikey(tmp_path, monkeypatch, caplog):
    """M2:自动生成管理员时,apikey 只以脱敏形态(前 6 后 4)进日志,明文不落盘。"""
    import logging
    _sqlite_env(tmp_path, monkeypatch)
    monkeypatch.delenv("ADMIN_APIKEY", raising=False)  # 强制走自动生成路径
    with caplog.at_level(logging.INFO, logger="common.apikey_mgmt"):
        billing_mgmt.ensure_admin("sentiment")
    rows = db.query("SELECT apikey FROM agent_api_keys "
                    "WHERE agent='sentiment' AND role='admin' AND status='active'")
    assert len(rows) == 1
    full_key = rows[0]["apikey"]
    assert full_key not in caplog.text            # 明文凭据不落日志
    assert billing_mgmt._mask_apikey(full_key) in caplog.text  # 脱敏形态可见


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
    r = billing_mgmt.update_apikey("sentiment", "k1", "sk-new012")
    assert r["new_apikey"] == "sk-new012" and r["migrated"] is True
    # 旧 key 失效(行已不存在 → _active_apikey 抛 401)
    with pytest.raises(Exception) as e:
        billing.usage("k1", "sentiment")
    assert getattr(e.value, "status_code", None) == 401
    # 新 key 额度/role 继承(free/paid 四元组一致,role 不变)
    u = billing.usage("sk-new012", "sentiment")
    assert u["free"] == {"total": 5, "used": 1, "remaining": 4}
    assert u["paid"] == {"total": 3, "used": 2, "remaining": 1}
    assert u["role"] == "normal"
    # 流水 apikey 已重写为 new
    rec = db.query("SELECT apikey FROM agent_billing_records "
                   "WHERE agent=%s AND bill_no=%s", ("sentiment", "b1"))
    assert rec[0]["apikey"] == "sk-new012"


def test_update_apikey_atomic(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed("k1", "sentiment")
    billing.create_pending("k1", "sentiment", "b1")
    # 强制流水 UPDATE 失败(SQLite 触发器 RAISE)→ 事务应整体回滚,不留半更新
    db.execute("CREATE TRIGGER trg_fail_update BEFORE UPDATE ON agent_billing_records "
               "BEGIN SELECT RAISE(ABORT, 'forced'); END")
    try:
        with pytest.raises(Exception):
            billing_mgmt.update_apikey("sentiment", "k1", "sk-new012")
    finally:
        db.execute("DROP TRIGGER trg_fail_update")
    # 主键未改:old key 仍有效、new key 不存在、流水 apikey 仍是 k1
    assert billing.usage("k1", "sentiment")["free"]["total"] == 10
    assert db.query("SELECT apikey FROM agent_api_keys WHERE agent=%s",
                    ("sentiment",))[0]["apikey"] == "k1"
    assert db.query("SELECT apikey FROM agent_billing_records "
                    "WHERE agent=%s AND bill_no=%s", ("sentiment", "b1"))[0]["apikey"] == "k1"


def test_update_apikey_rejects_bad_format(tmp_path, monkeypatch):
    """update_apikey 对 new_apikey 加 sk- 格式校验(终审 I2:旧 sentiment 400 拒绝,
    公共版曾丢校验直接 200 落库非法值)→ 非法 400,与 create 语义对齐。"""
    _sqlite_env(tmp_path, monkeypatch)
    _seed("sk-old01", "sentiment")
    with pytest.raises(Exception) as e:
        billing_mgmt.update_apikey("sentiment", "sk-old01", "not_a_key")
    assert getattr(e.value, "status_code", None) == 400
    # 落库不变:旧 key 仍在,非法新 key 未插入
    assert billing.usage("sk-old01", "sentiment")["free"]["total"] == 10


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


# ===== 4. 公共鉴权(common/auth.py,Task 4) =====


def test_auth_check_apikey(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed("k1", "sentiment")
    row = auth.check_apikey("k1", "sentiment")
    assert row["apikey"] == "k1" and row["agent"] == "sentiment"
    # 无行 → 401
    with pytest.raises(Exception) as e:
        auth.check_apikey("k-none", "sentiment")
    assert getattr(e.value, "status_code", None) == 401
    # 非 active → 401
    db.execute("UPDATE agent_api_keys SET status='deleted' "
               "WHERE apikey='k1' AND agent='sentiment'")
    with pytest.raises(Exception) as e:
        auth.check_apikey("k1", "sentiment")
    assert getattr(e.value, "status_code", None) == 401


def test_auth_require_admin(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed("k1", "sentiment")
    with pytest.raises(Exception) as e:
        auth.require_admin("k1", "sentiment")  # normal → 403
    assert getattr(e.value, "status_code", None) == 403
    db.execute("UPDATE agent_api_keys SET role='admin' "
               "WHERE apikey='k1' AND agent='sentiment'")
    auth.require_admin("k1", "sentiment")  # admin → 不抛
    with pytest.raises(Exception) as e:
        auth.require_admin("k1", "contract")  # 无行 → 401
    assert getattr(e.value, "status_code", None) == 401


def test_auth_assert_owner(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed("k1", "sentiment")
    _seed("k2", "sentiment")
    _seed("admin1", "sentiment")
    db.execute("UPDATE agent_api_keys SET role='admin' "
               "WHERE apikey='admin1' AND agent='sentiment'")
    auth.assert_owner("k1", "k1", "sentiment")  # 本人 → 不抛
    with pytest.raises(Exception) as e:
        auth.assert_owner("k1", "k2", "sentiment")  # 非本人且未传管理员 → 403
    assert getattr(e.value, "status_code", None) == 403
    auth.assert_owner("k1", "k2", "sentiment", admin="admin1")  # 同 agent 管理员放行 → 不抛
    with pytest.raises(Exception) as e:
        auth.assert_owner("k1", "k2", "sentiment", admin="k1")  # 传的管理员非 admin → 403
    assert getattr(e.value, "status_code", None) == 403


def test_auth_assert_owner_admin_is_per_agent(tmp_path, monkeypatch):
    """管理员判定按 agent 隔离:contract 的 admin 不能放行 sentiment 资源(M1)。

    场景:admin_c 只在 agent='contract' 是 admin,sentiment 无该 key → 跨 agent 非管理员
    不放行;sentiment 自己的 admin 才放行。
    """
    _sqlite_env(tmp_path, monkeypatch)
    _seed("k1", "sentiment")              # 普通用户(sentiment)
    _seed("k2", "sentiment")              # 他人资源 owner
    _seed("admin_c", "contract")          # 仅 contract 的 admin
    db.execute("UPDATE agent_api_keys SET role='admin' "
               "WHERE apikey='admin_c' AND agent='contract'")
    _seed("admin_s", "sentiment")         # sentiment 自己的 admin
    db.execute("UPDATE agent_api_keys SET role='admin' "
               "WHERE apikey='admin_s' AND agent='sentiment'")
    with pytest.raises(Exception) as e:
        auth.assert_owner("k1", "k2", "sentiment", admin="admin_c")  # 跨 agent admin → 403
    assert getattr(e.value, "status_code", None) == 403
    auth.assert_owner("k1", "k2", "sentiment", admin="admin_s")  # 本 agent admin → 放行


# ===== 5. 存量迁移(scripts/migrate_billing.py,Task 5) =====
# 老表 api_keys/billing_records 已由 _sqlite_env → db.init_tables() 建好(含
# created_at/updated_at 等默认列),无需手动 CREATE,直接 INSERT 指定列造数据。


def test_migrate_sentiment(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    db.execute("INSERT INTO api_keys (apikey, role, status, free_quota, paid_quota, "
               "free_used, paid_used) VALUES (%s,%s,%s,%s,%s,%s,%s)",
               ("k1", "admin", "active", 100, 5, 2, 1))
    db.execute("INSERT INTO billing_records (apikey, group_id, status) VALUES (%s,%s,%s)",
               ("k1", "g1", "committed"))
    from scripts.migrate_billing import migrate
    stats = migrate(dry_run=False)
    assert stats["keys"] == 1 and stats["records"] == 1
    rows = db.query("SELECT apikey, agent, free_quota FROM agent_api_keys WHERE agent='sentiment'")
    assert rows[0]["apikey"] == "k1" and rows[0]["free_quota"] == 100
    rec = db.query("SELECT bill_no, status FROM agent_billing_records WHERE agent='sentiment'")
    assert rec[0]["bill_no"] == "g1" and rec[0]["status"] == "committed"
    # 幂等:再跑不重复
    migrate(dry_run=False)
    assert db.query("SELECT COUNT(*) n FROM agent_api_keys")[0]["n"] == 1
    assert db.query("SELECT COUNT(*) n FROM agent_billing_records")[0]["n"] == 1


def test_migrate_dry_run_no_write(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    db.execute("INSERT INTO api_keys (apikey, role, status, free_quota, paid_quota, "
               "free_used, paid_used) VALUES (%s,%s,%s,%s,%s,%s,%s)",
               ("k1", "normal", "active", 10, 0, 0, 0))
    from scripts.migrate_billing import migrate
    stats = migrate(dry_run=True)
    assert stats["keys"] == 1 and stats["records"] == 0
    # dry-run 只统计不写
    assert db.query("SELECT COUNT(*) n FROM agent_api_keys")[0]["n"] == 0
    assert db.query("SELECT COUNT(*) n FROM agent_billing_records")[0]["n"] == 0


def test_migrate_rerun_ignores_new_rows(tmp_path, monkeypatch):
    """M4-①:部署后新用户/任务行出现时重跑不误报"行数不等"(校验幂等子集语义)。"""
    _sqlite_env(tmp_path, monkeypatch)
    db.execute("INSERT INTO api_keys (apikey, role, status, free_quota, paid_quota, "
               "free_used, paid_used) VALUES (%s,%s,%s,%s,%s,%s,%s)",
               ("k1", "normal", "active", 10, 0, 0, 0))
    db.execute("INSERT INTO billing_records (apikey, group_id, status) VALUES (%s,%s,%s)",
               ("k1", "g1", "committed"))
    from scripts.migrate_billing import migrate
    migrate(dry_run=False)
    # 模拟部署后新用户注册 + 新任务(不在迁移源表内)
    db.execute("INSERT INTO agent_api_keys (apikey, agent, free_quota, paid_quota) "
               "VALUES (%s,%s,%s,%s)", ("sk-newuser", "sentiment", 10, 0))
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no, status) "
               "VALUES (%s,%s,%s,%s)", ("sk-newuser", "sentiment", "g-new", "pending"))
    # 重跑:不因新表额外行误报,仍通过校验
    migrate(dry_run=False)


def test_migrate_preserves_timestamps(tmp_path, monkeypatch):
    """M4-②:老表 created_at/committed_at 迁移保真(有则带,无则 DEFAULT)。"""
    _sqlite_env(tmp_path, monkeypatch)
    db.execute("INSERT INTO api_keys (apikey, role, status, free_quota, paid_quota, "
               "free_used, paid_used) VALUES (%s,%s,%s,%s,%s,%s,%s)",
               ("k1", "normal", "active", 10, 0, 0, 0))
    db.execute("INSERT INTO billing_records (apikey, group_id, status, quota_type, "
               "created_at, committed_at) VALUES (%s,%s,%s,%s,%s,%s)",
               ("k1", "g1", "committed", "free", "2026-08-01 09:30:00", "2026-08-02 10:00:00"))
    from scripts.migrate_billing import migrate
    migrate(dry_run=False)
    rec = db.query("SELECT created_at, committed_at FROM agent_billing_records "
                   "WHERE agent='sentiment' AND bill_no='g1'")[0]
    assert rec["created_at"] == "2026-08-01 09:30:00"
    assert rec["committed_at"] == "2026-08-02 10:00:00"
    # 无 committed_at 的记录 → 落 DEFAULT(NULL)
    db.execute("INSERT INTO billing_records (apikey, group_id, status, created_at) "
               "VALUES (%s,%s,%s,%s)", ("k1", "g2", "pending", "2026-08-03 08:00:00"))
    migrate(dry_run=False)
    rec2 = db.query("SELECT created_at, committed_at FROM agent_billing_records "
                    "WHERE agent='sentiment' AND bill_no='g2'")[0]
    assert rec2["created_at"] == "2026-08-03 08:00:00"
    assert rec2["committed_at"] is None


def test_is_super_admin(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ADMIN_APIKEY", "sk-super")
    assert auth.is_super_admin("sk-super") is True
    assert auth.is_super_admin("sk-other") is False
    monkeypatch.delenv("ADMIN_APIKEY", raising=False)
    assert auth.is_super_admin("sk-super") is False  # 未配 → 恒 False(空 token 不匹配)
    assert auth.is_super_admin("") is False


# ===== 6. 角色管理(common/apikey_mgmt.py,Task 2) =====


def test_set_role(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    user = billing_mgmt.create_apikey("sentiment", "u")["apikey"]
    admin = billing_mgmt.create_apikey("sentiment", "a", role="admin")["apikey"]
    r = billing_mgmt.set_role("sentiment", user, "admin", admin)
    assert r["role"] == "admin"
    assert billing_mgmt._get_row(user, "sentiment")["role"] == "admin"
    with pytest.raises(Exception) as e:
        billing_mgmt.set_role("sentiment", user, "bogus", admin)  # 非法 role → 400
    assert getattr(e.value, "status_code", None) == 400
    with pytest.raises(Exception) as e:
        billing_mgmt.set_role("sentiment", admin, "normal", admin)  # 不可改自己 → 403
    assert getattr(e.value, "status_code", None) == 403
    with pytest.raises(Exception) as e:
        billing_mgmt.set_role("sentiment", "sk-nope", "normal", admin)  # 不存在 → 404
    assert getattr(e.value, "status_code", None) == 404


def test_set_role_normal_user_cannot_promote(tmp_path, monkeypatch):
    """非管理员不能改他人角色(require_admin 前置)。"""
    _sqlite_env(tmp_path, monkeypatch)
    u1 = billing_mgmt.create_apikey("sentiment", "u1")["apikey"]
    u2 = billing_mgmt.create_apikey("sentiment", "u2")["apikey"]
    with pytest.raises(Exception) as e:
        billing_mgmt.set_role("sentiment", u2, "admin", u1)  # u1 非管理员 → 403
    assert getattr(e.value, "status_code", None) == 403


def test_deactivate_super_admin_bypasses_agent_row(tmp_path, monkeypatch):
    """超管放行:新 agent 无 (ADMIN_APIKEY, agent) 行也能停用(M1 修正)。"""
    _sqlite_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ADMIN_APIKEY", "sk-super")
    user = billing_mgmt.create_apikey("brand_new_agent", "u")["apikey"]
    billing_mgmt.deactivate_apikey("brand_new_agent", user, "sk-super")  # 无超管行 → 不再 401
    assert billing_mgmt._get_row(user, "brand_new_agent")["status"] == "deleted"


# ===== 7. create_apikey 额度参数 + list_keys/list_agents(common/apikey_mgmt.py,Task 3) =====


def test_create_apikey_custom_quota(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    k = billing_mgmt.create_apikey("sentiment", "x", role="admin", free_quota=50, paid_quota=7)
    assert k["free_quota"] == 50 and k["paid_quota"] == 7
    row = billing_mgmt._get_row(k["apikey"], "sentiment")
    assert row["free_quota"] == 50 and row["paid_quota"] == 7 and row["role"] == "admin"
    # 缺省不变 + 向后兼容
    k2 = billing_mgmt.create_apikey("sentiment", "y")
    assert k2["free_quota"] == 10 and k2["paid_quota"] == 0
    with pytest.raises(ValueError):
        billing_mgmt.create_apikey("sentiment", "z", free_quota=-1)  # 负额度拒绝


def test_list_keys_cross_agent_includes_all(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    admin = billing_mgmt.create_apikey("sentiment", "a1", role="admin")["apikey"]
    u1 = billing_mgmt.create_apikey("sentiment", "u1")["apikey"]
    u2 = billing_mgmt.create_apikey("contract", "u2")["apikey"]
    rows = billing_mgmt.list_keys()
    apis = {(r["agent"], r["apikey"], r["role"]) for r in rows}
    assert ("sentiment", u1, "normal") in apis
    assert ("sentiment", admin, "admin") in apis   # 含 admin
    assert ("contract", u2, "normal") in apis      # 跨 agent
    assert all(r["agent"] == "sentiment" for r in billing_mgmt.list_keys(agent="sentiment"))
    billing_mgmt.deactivate_apikey("sentiment", u1, admin)
    assert any(r["apikey"] == u1 and r["status"] == "deleted" for r in billing_mgmt.list_keys())  # 软删也列出


def test_list_agents_counts_active(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    billing_mgmt.create_apikey("sentiment", "u1")
    billing_mgmt.create_apikey("sentiment", "u2")
    billing_mgmt.create_apikey("contract", "u3")
    agents = {a["agent"]: a["key_count"] for a in billing_mgmt.list_agents()}
    assert agents == {"sentiment": 2, "contract": 1}
    # 软删后 active 计数应下降:证明 WHERE status='active' 过滤生效(名不副实补齐)
    admin = billing_mgmt.create_apikey("sentiment", "a", role="admin")["apikey"]
    u1 = db.query("SELECT apikey FROM agent_api_keys WHERE agent='sentiment' "
                  "AND role='normal' ORDER BY apikey")[0]["apikey"]
    billing_mgmt.deactivate_apikey("sentiment", u1, admin)
    agents2 = {a["agent"]: a["key_count"] for a in billing_mgmt.list_agents()}
    assert agents2["sentiment"] == 2  # 原 2 normal + 1 admin,软删 1 normal → 剩 1 normal + 1 admin = 2


# ===== 8. 报表(common/billing.py,Task 4) =====


def test_report_summary_active_only(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    db.execute("INSERT INTO agent_api_keys (apikey, agent, free_quota, free_used, paid_quota, paid_used) "
               "VALUES (%s,%s,%s,%s,%s,%s)", ("k1", "sentiment", 10, 3, 5, 1))
    db.execute("INSERT INTO agent_api_keys (apikey, agent, status, free_quota, free_used) "
               "VALUES (%s,%s,%s,%s,%s)", ("k2", "sentiment", "deleted", 10, 9))  # 软删不入汇总
    db.execute("INSERT INTO agent_api_keys (apikey, agent, free_quota, free_used) "
               "VALUES (%s,%s,%s,%s)", ("k3", "contract", 10, 1))
    s = billing.report_summary()
    by_agent = {a["agent"]: a for a in s["agents"]}
    assert by_agent["sentiment"]["key_count"] == 1  # k2 软删不计数
    assert by_agent["sentiment"]["free_used"] == 3
    assert by_agent["sentiment"]["free_remaining"] == 7
    assert by_agent["sentiment"]["paid_used"] == 1 and by_agent["sentiment"]["paid_remaining"] == 4
    assert s["total"]["key_count"] == 2
    assert s["total"]["free_used"] == 4 and s["total"]["paid_remaining"] == 4


def test_report_history_committed_by_day(tmp_path, monkeypatch):
    # 日期相对 now 动态生成(report_history cutoff = now - days,硬编码绝对日期会在
    # 越过 cutoff 后把这行排除 → 断言 KeyError,~2.5 周后必挂)。
    d3 = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    _sqlite_env(tmp_path, monkeypatch)
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no, status, committed_at) "
               "VALUES (%s,%s,%s,%s,%s)", ("k1", "sentiment", "b1", "committed", f"{d3} 09:00:00"))
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no, status, committed_at) "
               "VALUES (%s,%s,%s,%s,%s)", ("k1", "sentiment", "b2", "committed", f"{d3} 10:00:00"))
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no, status, committed_at) "
               "VALUES (%s,%s,%s,%s,%s)", ("k1", "contract", "b3", "committed", f"{d3} 11:00:00"))
    # 非 committed 不入:带 in-range committed_at 的 pending 行,若漏掉 status 过滤会误入
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no, status, committed_at) "
               "VALUES (%s,%s,%s,%s,%s)", ("k1", "sentiment", "b4", "pending", f"{d3} 12:00:00"))
    h = billing.report_history(days=30)
    series = {(s["date"], s["agent"]): s["committed"] for s in h["series"]}
    assert series[(d3, "sentiment")] == 2
    assert series[(d3, "contract")] == 1
    # agent 过滤
    h2 = billing.report_history(agent="sentiment", days=30)
    assert all(s["agent"] == "sentiment" for s in h2["series"])
    assert len(h2["series"]) == 1
    # days=1(今天)cutoff 排除历史:动态新日期不在、更老日期同样被排除
    h3 = billing.report_history(days=1)
    assert all(s["date"] != d3 for s in h3["series"])
    old = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no, status, committed_at) "
               "VALUES (%s,%s,%s,%s,%s)", ("k1", "sentiment", "b5", "committed", f"{old} 08:00:00"))
    h4 = billing.report_history(days=30)
    assert all(s["date"] != old for s in h4["series"])
