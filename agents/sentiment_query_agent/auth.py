"""鉴权:apikey 校验 + 管理员校验 + 资源归属校验。

设计见 docs/superpowers/specs/2026-08-11-quota-billing-stats-design.md §5/§7。

- apikey 即用户,存 MySQL api_keys 表;`Authorization: Bearer <apikey>`。
- 管理员:role='admin'(.env ADMIN_APIKEY 启动时写入),不受权限控制。
- 归属:group.owner = apikey;跨 apikey 403,管理员放行。
- API_KEYS_JSON 废弃(apikey 全在 MySQL)。
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from common import config, db


def _get_apikey(apikey: str) -> dict | None:
    rows = db.query("SELECT * FROM api_keys WHERE apikey=%s", (apikey,))
    return rows[0] if rows else None


def authenticate(request: Request) -> str:
    """校验 Bearer apikey(存在且 active),返回 apikey 本身。无效 → 401。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Authorization: Bearer <apikey>")
    apikey = auth[7:].strip()
    row = _get_apikey(apikey)
    if row is None or row["status"] != "active":
        raise HTTPException(status_code=401, detail="apikey 无效或已删除")
    return apikey


def require_admin(apikey: str) -> None:
    """管理接口校验:role='admin',否则 403。"""
    row = _get_apikey(apikey)
    if row is None or row["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")


def is_admin(apikey: str) -> bool:
    row = _get_apikey(apikey)
    return bool(row and row["role"] == "admin")


def assert_owner(user: str, group: dict) -> None:
    """资源归属校验:group.owner 必须等于 user;管理员放行。"""
    if is_admin(user):
        return
    if group.get("owner") != user:
        raise HTTPException(status_code=403, detail="无权访问该方案组")


def admin_apikey() -> str:
    """管理员 apikey(.env ADMIN_APIKEY)。"""
    return config.get_env("ADMIN_APIKEY", "")
