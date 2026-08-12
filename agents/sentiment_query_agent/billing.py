"""配额与资费:api_keys + billing_records(MySQL)。

设计见 docs/superpowers/specs/2026-08-11-quota-billing-stats-design.md。

- 用户即 apikey;免费额度(初始 10)+ 付费额度(充值),commit 扣减(先免费后付费)。
- 并发:pending 上限 5;额度扣减用事务保证原子。
- 接口签名与旧版兼容(create_pending/commit/cancel_pending),api.py 调用不变。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException

from common import db

_MAX_PENDING = 5  # 同一 apikey 最多并发 pending 数


def get_apikey(apikey: str) -> dict | None:
    """查 apikey 记录(含 deleted)。"""
    rows = db.query("SELECT * FROM api_keys WHERE apikey=%s", (apikey,))
    return rows[0] if rows else None


def _active_apikey(apikey: str) -> dict:
    """查 active apikey,无效/删除 → 401。"""
    row = get_apikey(apikey)
    if row is None or row["status"] != "active":
        raise HTTPException(status_code=401, detail="apikey 无效或已删除")
    return row


def check_quota(apikey: str) -> None:
    """提交时校验额度:free_remaining + paid_remaining > 0,否则 403。"""
    row = _active_apikey(apikey)
    free_left = row["free_quota"] - row["free_used"]
    paid_left = row["paid_quota"] - row["paid_used"]
    if free_left + paid_left <= 0:
        raise HTTPException(status_code=403, detail="额度不足,请联系管理员充值")


def create_pending(apikey: str, group_id: str) -> None:
    """创建 group 时记 pending(并发上限 5)。"""
    row = _active_apikey(apikey)
    rows = db.query(
        "SELECT COUNT(*) AS n FROM billing_records WHERE apikey=%s AND status='pending'",
        (apikey,),
    )
    if rows[0]["n"] >= _MAX_PENDING:
        raise HTTPException(status_code=429, detail="并发 pending 超限,请先完成或取消未入库的方案组")
    db.execute(
        "INSERT INTO billing_records (apikey, group_id, status) VALUES (%s, %s, 'pending')",
        (apikey, group_id),
    )


def commit(apikey: str, group_id: str) -> None:
    """commit:转正式计费(1 单位),额度扣减(先 free 后 paid)。事务保证原子。"""

    @db.transaction
    def _do(cur, exec) -> None:
        # 1. 更新计费记录(pending → committed)
        n = exec(
            "UPDATE billing_records SET status='committed', committed_at=NOW(), "
            "quota_type=%s WHERE group_id=%s AND status='pending'",
            ("free", group_id),
        )
        if n == 0:
            # pending 不存在,404
            rows = exec(
                "SELECT id FROM billing_records WHERE group_id=%s AND status='pending'",
                (group_id,),
            )
            if not rows:
                raise HTTPException(status_code=404, detail="计费记录不存在(group 未创建或无 pending)")
            raise RuntimeError("计费记录更新失败")

        # 2. 额度扣减:先免费,免费用完扣付费
        rows = exec("SELECT * FROM api_keys WHERE apikey=%s FOR UPDATE", (apikey,))
        key = rows[0] if rows else None
        if key is None:
            raise RuntimeError(f"apikey {apikey} 不存在")
        if key["free_used"] < key["free_quota"]:
            exec("UPDATE api_keys SET free_used=free_used+1 WHERE apikey=%s", (apikey,))
            quota_type = "free"
        else:
            exec("UPDATE api_keys SET paid_used=paid_used+1 WHERE apikey=%s", (apikey,))
            quota_type = "paid"
        # 修正 quota_type(上面先标了 free,若实际扣 paid 需回写)
        exec(
            "UPDATE billing_records SET quota_type=%s WHERE group_id=%s",
            (quota_type, group_id),
        )

    _do()


def cancel_pending(apikey: str, group_id: str) -> None:
    """取消 pending(stop 时调用):释放并发额度,不扣额度。"""
    db.execute(
        "UPDATE billing_records SET status='cancelled' WHERE group_id=%s AND status='pending'",
        (group_id,),
    )


def list_pending(apikey: str) -> list[dict]:
    """查当前 apikey 的 pending 任务。"""
    return db.query(
        "SELECT group_id, created_at FROM billing_records "
        "WHERE apikey=%s AND status='pending' ORDER BY created_at DESC",
        (apikey,),
    )


def usage(apikey: str) -> dict:
    """资费查询:当前 apikey 的额度使用情况。"""
    row = _active_apikey(apikey)
    pending = db.query(
        "SELECT COUNT(*) AS n FROM billing_records WHERE apikey=%s AND status='pending'",
        (apikey,),
    )[0]["n"]
    return {
        "apikey": apikey,
        "role": row["role"],
        "free": {
            "total": row["free_quota"],
            "used": row["free_used"],
            "remaining": row["free_quota"] - row["free_used"],
        },
        "paid": {
            "total": row["paid_quota"],
            "used": row["paid_used"],
            "remaining": row["paid_quota"] - row["paid_used"],
        },
        "pending_count": pending,
    }


def usage_all() -> list[dict]:
    """管理员:所有普通用户 apikey 的额度(按 apikey 分类)。"""
    rows = db.query(
        "SELECT apikey, free_quota, free_used, paid_quota, paid_used, role, status "
        "FROM api_keys WHERE role='normal' ORDER BY apikey"
    )
    result = []
    for r in rows:
        if r["status"] != "active":
            continue
        result.append({
            "apikey": r["apikey"],
            "free": {"total": r["free_quota"], "used": r["free_used"],
                     "remaining": r["free_quota"] - r["free_used"]},
            "paid": {"total": r["paid_quota"], "used": r["paid_used"],
                     "remaining": r["paid_quota"] - r["paid_used"]},
        })
    return result


def add_free_quota(apikey: str, count: int) -> None:
    """管理员:增加免费额度。"""
    _active_apikey(apikey)
    db.execute("UPDATE api_keys SET free_quota=free_quota+%s WHERE apikey=%s", (count, apikey))


def add_paid_quota(apikey: str, count: int) -> None:
    """管理员:增加付费额度。"""
    _active_apikey(apikey)
    db.execute("UPDATE api_keys SET paid_quota=paid_quota+%s WHERE apikey=%s", (count, apikey))
