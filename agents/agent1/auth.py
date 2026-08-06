"""鉴权:apikey 校验 + 资源归属校验。

设计见 docs/superpowers/specs/2026-08-06-agent1-sentiment-query-agent-design.md §8。

- apikey:`Authorization: Bearer <apikey>`,合法 key 列表配 .env(JSON 格式)。
- 归属:每个 group 记录 owner(apikey 标识的用户),越权 403。
"""

from __future__ import annotations

import json

from fastapi import HTTPException, Request

from common import config


def _valid_keys() -> dict[str, str]:
    """apikey → 用户标识 映射(从 .env API_KEYS_JSON 读,JSON 格式)。"""
    raw = config.get_env("API_KEYS_JSON")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def authenticate(request: Request) -> str:
    """校验 Bearer apikey,返回用户标识。无效 → 401。

    用法(FastAPI 依赖):
        from fastapi import Depends
        def _auth(request: Request) -> str:
            return authenticate(request)
        @app.get(...)
        def x(user: str = Depends(_auth)): ...
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Authorization: Bearer <apikey>")
    apikey = auth[7:].strip()
    user = _valid_keys().get(apikey)
    if not user:
        raise HTTPException(status_code=401, detail="apikey 无效")
    return user


def assert_owner(user: str, group: dict) -> None:
    """资源归属校验:group.owner 必须等于 user,否则 403。"""
    if group.get("owner") != user:
        raise HTTPException(status_code=403, detail="无权访问该方案组")
