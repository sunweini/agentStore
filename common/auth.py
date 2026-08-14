"""公共鉴权:apikey 校验 + 管理员校验 + 资源归属校验。

统一表 agent_api_keys(复合主键 apikey+agent),设计见
docs/superpowers/specs/2026-08-14-common-billing-component-design.md §4。

- check_apikey(apikey, agent):请求 apikey 校验(存在 + active),无效/删除 → 401。
- require_admin(apikey, agent):管理员校验,check_apikey 后 role != 'admin' → 403。
- assert_owner(user, owner, admin=None):资源归属校验,非本人且非管理员 → 403。

存储访问统一走 common/db.py(MySQL 生产 / SQLite 测试双后端)。
"""

from __future__ import annotations

from fastapi import HTTPException

from common import db


def check_apikey(apikey: str, agent: str) -> dict:
    """校验 (apikey, agent) 行存在且 active,返回行;无效/删除 → 401。"""
    rows = db.query("SELECT * FROM agent_api_keys WHERE apikey=%s AND agent=%s",
                    (apikey, agent))
    row = rows[0] if rows else None
    if row is None or row["status"] != "active":
        raise HTTPException(status_code=401, detail="apikey 无效或已删除")
    return row


def require_admin(apikey: str, agent: str) -> None:
    """管理接口校验:该 agent 下 apikey 存在 + active 且 role='admin',否则 403。"""
    row = check_apikey(apikey, agent)
    if row["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def assert_owner(user: str, owner: str, admin: str | None = None) -> None:
    """资源归属校验:非本人且非管理员 → 403(管理员放行)。

    本人(user == owner)直接放行;admin 提供时校验其为系统内 active 管理员后放行。
    admin 判定跨 agent(签名无 agent):管理员 apikey 是全局 ADMIN_APIKEY,各 agent
    经 ensure_admin 注册同一字符串,故查任一 agent 的 active 管理员行即可。
    """
    if user == owner:
        return
    if admin is not None and _is_admin(admin):
        return
    raise HTTPException(status_code=403, detail="无权访问该资源")


def _is_admin(apikey: str) -> bool:
    """apikey 是否为系统内 active 管理员(任一 agent 注册的行)。"""
    rows = db.query("SELECT role, status FROM agent_api_keys WHERE apikey=%s "
                    "AND role='admin' AND status='active'", (apikey,))
    return bool(rows)
