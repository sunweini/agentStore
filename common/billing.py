"""公共计费:一套逻辑,额度按 (apikey, agent) 区分。

单表收敛(agent_api_keys / agent_billing_records),设计见
docs/superpowers/specs/2026-08-14-common-billing-component-design.md。
扣费状态机 pending→committed/cancelled;先免费后付费,事务原子;pending 上限 5。
"""
from __future__ import annotations

from fastapi import HTTPException

from common import db

_MAX_PENDING = 5
_ADMIN_FREE_QUOTA = 99999999


def _active_apikey(apikey: str, agent: str) -> dict:
    rows = db.query("SELECT * FROM agent_api_keys WHERE apikey=%s AND agent=%s",
                    (apikey, agent))
    row = rows[0] if rows else None
    if row is None or row["status"] != "active":
        raise HTTPException(status_code=401, detail="apikey 无效或已删除")
    return row


def check_quota(apikey: str, agent: str) -> None:
    row = _active_apikey(apikey, agent)
    if (row["free_quota"] - row["free_used"]) + (row["paid_quota"] - row["paid_used"]) <= 0:
        raise HTTPException(status_code=403, detail="额度不足,请联系管理员充值")


def create_pending(apikey: str, agent: str, bill_no: str) -> None:
    _active_apikey(apikey, agent)
    rows = db.query(
        "SELECT COUNT(*) AS n FROM agent_billing_records "
        "WHERE apikey=%s AND agent=%s AND status='pending'", (apikey, agent))
    if rows[0]["n"] >= _MAX_PENDING:
        raise HTTPException(status_code=429, detail="并发 pending 超限,请先完成或取消任务")
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no) "
               "VALUES (%s,%s,%s)", (apikey, agent, bill_no))


def commit(apikey: str, agent: str, bill_no: str) -> None:
    # 事务外前置 SELECT 判 pending 记录存在:无行 → 404(事务包装会把异常包成 RuntimeError,
    # 404 语义必须在事务外触发才生效;事务内 UPDATE 0 行仍 RuntimeError 兜底防竞态)。
    # 按 apikey+agent+bill_no 三重过滤:防跨 apikey 命中 —— (agent, bill_no) 唯一约束
    # 只在同 agent 内有效,调用方用他人 bill_no 配自己 apikey 若不按 apikey 过滤,
    # 会把他人 pending 标 committed 并扣自己额度(终审 M6 安全)。
    if not db.query("SELECT id FROM agent_billing_records "
                    "WHERE apikey=%s AND agent=%s AND bill_no=%s",
                    (apikey, agent, bill_no)):
        raise HTTPException(status_code=404, detail="计费记录不存在")

    @db.transaction
    def _do(cur, exec) -> None:
        n = exec("UPDATE agent_billing_records SET status='committed', committed_at=NOW(), "
                 "quota_type='free' WHERE apikey=%s AND agent=%s AND bill_no=%s "
                 "AND status='pending'", (apikey, agent, bill_no))
        if n == 0:
            # 前置 SELECT 已判记录存在,走到此处说明状态非 pending(已 committed/cancelled)→ 竞态兜底
            raise RuntimeError("计费记录更新失败")
        rows = exec("SELECT * FROM agent_api_keys WHERE apikey=%s AND agent=%s FOR UPDATE",
                    (apikey, agent))
        key = rows[0] if rows else None
        if key is None:
            raise RuntimeError("apikey 不存在")
        if key["free_used"] < key["free_quota"]:
            exec("UPDATE agent_api_keys SET free_used=free_used+1 WHERE apikey=%s AND agent=%s",
                 (apikey, agent))
            quota_type = "free"
        else:
            exec("UPDATE agent_api_keys SET paid_used=paid_used+1 WHERE apikey=%s AND agent=%s",
                 (apikey, agent))
            quota_type = "paid"
        exec("UPDATE agent_billing_records SET quota_type=%s "
             "WHERE apikey=%s AND agent=%s AND bill_no=%s",
             (quota_type, apikey, agent, bill_no))
    _do()


def cancel_pending(apikey: str, agent: str, bill_no: str) -> None:
    db.execute("UPDATE agent_billing_records SET status='cancelled' "
               "WHERE apikey=%s AND agent=%s AND bill_no=%s AND status='pending'",
               (apikey, agent, bill_no))


def usage(apikey: str, agent: str) -> dict:
    row = _active_apikey(apikey, agent)
    pending = db.query("SELECT COUNT(*) AS n FROM agent_billing_records "
                       "WHERE apikey=%s AND agent=%s AND status='pending'",
                       (apikey, agent))[0]["n"]
    return {
        "apikey": apikey, "agent": agent, "role": row["role"],
        "free": {"total": row["free_quota"], "used": row["free_used"],
                 "remaining": row["free_quota"] - row["free_used"]},
        "paid": {"total": row["paid_quota"], "used": row["paid_used"],
                 "remaining": row["paid_quota"] - row["paid_used"]},
        "pending_count": pending,
    }


def usage_all(agent: str | None = None) -> list[dict]:
    sql = ("SELECT apikey, agent, role, status, free_quota, free_used, "
           "paid_quota, paid_used FROM agent_api_keys WHERE role='normal' AND status='active'")
    params: tuple = ()
    if agent:
        sql += " AND agent=%s"
        params = (agent,)
    sql += " ORDER BY agent, apikey"  # 确定性顺序(终审 M5),便于对账/分页
    rows = db.query(sql, params)
    return [{"apikey": r["apikey"], "agent": r["agent"],
             "free": {"total": r["free_quota"], "used": r["free_used"],
                      "remaining": r["free_quota"] - r["free_used"]},
             "paid": {"total": r["paid_quota"], "used": r["paid_used"],
                      "remaining": r["paid_quota"] - r["paid_used"]}}
            for r in rows]


def add_free_quota(apikey: str, agent: str, count: int) -> None:
    _active_apikey(apikey, agent)
    db.execute("UPDATE agent_api_keys SET free_quota=free_quota+%s WHERE apikey=%s AND agent=%s",
               (count, apikey, agent))


def add_paid_quota(apikey: str, agent: str, count: int) -> None:
    _active_apikey(apikey, agent)
    db.execute("UPDATE agent_api_keys SET paid_quota=paid_quota+%s WHERE apikey=%s AND agent=%s",
               (count, apikey, agent))


def list_pending(apikey: str, agent: str) -> list[dict]:
    return db.query(
        "SELECT bill_no, created_at FROM agent_billing_records "
        "WHERE apikey=%s AND agent=%s AND status='pending' ORDER BY created_at DESC",
        (apikey, agent))
