"""独立 apikey 鉴权(contract 独立体系,不复用 sentiment auth)。

设计 §5/§7:contract 用户需单独创建 apikey(存 contract_api_keys 表),
额度与 sentiment 互不影响;独立 apikey 管理接口 POST /api/v1/apikeys。

- check_apikey:请求 apikey 校验(存在 + active),无效/删除 → 401。
- require_admin:管理员校验,role != 'admin' → 403。
- 存储访问统一走 common/db.py(MySQL 生产 / SQLite 测试双后端),业务代码不直接连库。

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""

from __future__ import annotations

from fastapi import HTTPException

from common import db


def check_apikey(apikey: str) -> dict:
    """校验 apikey(存在 + active),返回记录行;无效/删除 → 401。"""
    rows = db.query("SELECT * FROM contract_api_keys WHERE apikey=%s", (apikey,))
    row = rows[0] if rows else None
    if row is None or row["status"] != "active":
        raise HTTPException(status_code=401, detail="apikey 无效或已删除")
    return row


def require_admin(apikey: str) -> None:
    """管理接口校验:role='admin',否则 403。"""
    row = check_apikey(apikey)
    if row["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
