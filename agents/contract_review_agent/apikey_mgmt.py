"""独立 apikey 管理(contract 独立表,参照 sentiment apikey_mgmt 独立实现)。

设计 §5/§7:apikey 创建(默认免费 10 / 付费 0)/ 删除(软删,数据保留)/
管理员查询;额度与 sentiment 互不影响,业务代码不跨 agent import。

- create_apikey(name, role):生成随机 apikey(sk- 前缀 + 32 位十六进制),
  默认 free_quota=10 / paid_quota=0。name 是创建时的标签,仅返回不落库
  (表结构照 brief 无 name 列)。
- admin_list(apikey):管理员查询全部 apikey 的额度使用。
- deactivate_apikey(apikey, admin):管理员软删(status='deleted'),鉴权即拒绝。
- 存储访问统一走 common/db.py(MySQL 生产 / SQLite 测试双后端),业务代码不直接连库。

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException

from common import db

from agents.contract_review_agent.auth import require_admin

_DEFAULT_FREE_QUOTA = 10
_DEFAULT_PAID_QUOTA = 0


def _gen_apikey() -> str:
    """生成随机 apikey:sk- + 32 位十六进制(格式兼容 sk-[A-Za-z0-9])。"""
    return f"sk-{secrets.token_hex(16)}"


def create_apikey(name: str, role: str = "normal") -> dict:
    """创建 apikey(默认免费 10 / 付费 0),返回 {apikey, name, free_quota, paid_quota}。

    name 作为创建时标签仅出现在返回值中(表结构无 name 列,不落库)。
    随机 apikey 冲突(理论极小)时递归重试一次。
    """
    apikey = _gen_apikey()
    try:
        db.execute(
            "INSERT INTO contract_api_keys (apikey, role, status, free_quota, paid_quota) "
            "VALUES (%s, %s, 'active', %s, %s)",
            (apikey, role, _DEFAULT_FREE_QUOTA, _DEFAULT_PAID_QUOTA),
        )
    except RuntimeError as exc:
        if "Duplicate" in str(exc) or "1062" in str(exc) or "UNIQUE" in str(exc):
            return create_apikey(name, role)
        raise
    return {
        "apikey": apikey,
        "name": name,
        "role": role,
        "free_quota": _DEFAULT_FREE_QUOTA,
        "paid_quota": _DEFAULT_PAID_QUOTA,
    }


def admin_list(apikey: str) -> list[dict]:
    """管理员:查询全部 apikey 的额度使用(含 status,软删的也列出)。"""
    require_admin(apikey)
    rows = db.query(
        "SELECT apikey, role, status, free_quota, free_used, paid_quota, paid_used "
        "FROM contract_api_keys ORDER BY apikey"
    )
    return [
        {
            "apikey": r["apikey"],
            "role": r["role"],
            "status": r["status"],
            "free": {"total": r["free_quota"], "used": r["free_used"],
                     "remaining": r["free_quota"] - r["free_used"]},
            "paid": {"total": r["paid_quota"], "used": r["paid_used"],
                     "remaining": r["paid_quota"] - r["paid_used"]},
        }
        for r in rows
    ]


def deactivate_apikey(apikey: str, admin: str) -> None:
    """管理员软删 apikey:status='deleted',鉴权即拒绝,数据保留。"""
    require_admin(admin)
    row = db.query("SELECT * FROM contract_api_keys WHERE apikey=%s", (apikey,))
    if not row:
        raise HTTPException(status_code=404, detail="apikey 不存在")
    if row[0]["role"] == "admin":
        raise HTTPException(status_code=403, detail="不可停用管理员 apikey")
    db.execute(
        "UPDATE contract_api_keys SET status='deleted' WHERE apikey=%s",
        (apikey,),
    )
