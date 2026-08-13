"""独立配额与计费(contract_api_keys / contract_billing_records,与 sentiment 完全隔离)。

设计 §5:不复用 sentiment 的 billing/auth —— 同库(agentstore)独立表,独立 apikey,
额度与 sentiment 互不影响,业务代码不跨 agent import。
计费单位:按次 —— 一个合同文件审核完成 = 1 次扣费(先免费后付费,事务原子);
F1 prompt 优化默认不计费;pending 上限每 apikey 5。

- init_db:建全表(common.db.init_tables)。
- check_quota:免费+付费剩余 ≤0 → 403。
- create_pending:并发 pending 上限 5 → 429。
- commit:审核完成扣 1 单位(先 free 后 paid,事务原子)。
- cancel_pending:取消 pending,释放并发额度,不扣费。
- usage:查询当前 apikey 的额度使用。
- 存储访问统一走 common/db.py(MySQL 生产 / SQLite 测试双后端),业务代码不直接连库。

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""

from __future__ import annotations

from fastapi import HTTPException

from common import db

_MAX_PENDING = 5  # 同一 apikey 最多并发 pending 数


def init_db() -> None:
    """建全表(幂等)。生产 MySQL 建表走 deploy/init_tables.sql。"""
    db.init_tables()


def _active_apikey(apikey: str) -> dict:
    """查 active apikey,无效/删除 → 401。"""
    rows = db.query("SELECT * FROM contract_api_keys WHERE apikey=%s", (apikey,))
    row = rows[0] if rows else None
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


def create_pending(apikey: str, task_id: str) -> None:
    """创建任务时记 pending(并发上限 5)。"""
    _active_apikey(apikey)
    rows = db.query(
        "SELECT COUNT(*) AS n FROM contract_billing_records "
        "WHERE apikey=%s AND status='pending'",
        (apikey,),
    )
    if rows[0]["n"] >= _MAX_PENDING:
        raise HTTPException(status_code=429, detail="并发 pending 超限,请先完成或取消未审核的任务")
    db.execute(
        "INSERT INTO contract_billing_records (apikey, task_id, status) "
        "VALUES (%s, %s, 'pending')",
        (apikey, task_id),
    )


def commit(apikey: str, task_id: str) -> None:
    """commit:审核完成扣 1 单位(先免费后付费,事务保证原子)。"""

    @db.transaction
    def _do(cur, exec) -> None:
        # 1. 更新计费记录(pending → committed)
        n = exec(
            "UPDATE contract_billing_records SET status='committed', committed_at=NOW(), "
            "quota_type=%s WHERE task_id=%s AND status='pending'",
            ("free", task_id),
        )
        if n == 0:
            # pending 不存在
            rows = exec(
                "SELECT id FROM contract_billing_records "
                "WHERE task_id=%s AND status='pending'",
                (task_id,),
            )
            if not rows:
                raise HTTPException(status_code=404, detail="计费记录不存在(task 未创建或无 pending)")
            raise RuntimeError("计费记录更新失败")

        # 2. 额度扣减:先免费,免费用完扣付费
        rows = exec("SELECT * FROM contract_api_keys WHERE apikey=%s FOR UPDATE", (apikey,))
        key = rows[0] if rows else None
        if key is None:
            raise RuntimeError(f"apikey {apikey} 不存在")
        if key["free_used"] < key["free_quota"]:
            exec("UPDATE contract_api_keys SET free_used=free_used+1 WHERE apikey=%s", (apikey,))
            quota_type = "free"
        else:
            exec("UPDATE contract_api_keys SET paid_used=paid_used+1 WHERE apikey=%s", (apikey,))
            quota_type = "paid"
        # 修正 quota_type(上面先标了 free,若实际扣 paid 需回写)
        exec(
            "UPDATE contract_billing_records SET quota_type=%s WHERE task_id=%s",
            (quota_type, task_id),
        )

    _do()


def cancel_pending(apikey: str, task_id: str) -> None:
    """取消 pending(stop/失败时调用):释放并发额度,不扣费。"""
    db.execute(
        "UPDATE contract_billing_records SET status='cancelled' "
        "WHERE apikey=%s AND task_id=%s AND status='pending'",
        (apikey, task_id),
    )


def usage(apikey: str) -> dict:
    """资费查询:当前 apikey 的额度使用情况。"""
    row = _active_apikey(apikey)
    pending = db.query(
        "SELECT COUNT(*) AS n FROM contract_billing_records "
        "WHERE apikey=%s AND status='pending'",
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
