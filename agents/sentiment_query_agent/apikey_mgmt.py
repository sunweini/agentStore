"""apikey 管理:创建/修改/删除 + 启动初始化管理员。

设计见 docs/superpowers/specs/2026-08-11-quota-billing-stats-design.md §3/§5。

- 创建:默认 free_quota=10, paid_quota=0, role=normal。
- 修改:旧 key → 新 key,资费继承(api_keys 换主键 + billing_records 迁移 + 方案组 owner 迁移)。
- 删除:软删(status='deleted'),数据保留但鉴权拒绝。
- 初始化:启动时确保管理员 key 存在(.env ADMIN_APIKEY,额度 99999999)。
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException

from common import db

_ADMIN_FREE_QUOTA = 99999999


def _validate_apikey(apikey: str) -> None:
    """apikey 格式校验:sk- 开头,6-64 位字母数字。"""
    if not re.fullmatch(r"sk-[A-Za-z0-9]{6,64}", apikey):
        raise HTTPException(status_code=400, detail="apikey 格式:sk- 开头 + 6-64 位字母数字")


def create_apikey(apikey: str) -> dict:
    """创建 apikey(默认免费 10/付费 0)。冲突 → 409。"""
    _validate_apikey(apikey)
    try:
        db.execute(
            "INSERT INTO api_keys (apikey, role, status, free_quota, paid_quota) "
            "VALUES (%s, 'normal', 'active', 10, 0)",
            (apikey,),
        )
    except RuntimeError as exc:
        if "Duplicate" in str(exc) or "1062" in str(exc):
            raise HTTPException(status_code=409, detail="apikey 已存在") from exc
        raise
    return {"apikey": apikey, "free_quota": 10, "paid_quota": 0}


def update_apikey(old_key: str, new_key: str) -> dict:
    """修改 apikey:旧 key → 新 key,资费继承 + 历史迁移(计费记录 + 方案组文件)。"""
    _validate_apikey(new_key)
    old_row = db.query("SELECT * FROM api_keys WHERE apikey=%s", (old_key,))
    if not old_row:
        raise HTTPException(status_code=404, detail="原 apikey 不存在")
    if old_row[0]["role"] == "admin":
        raise HTTPException(status_code=403, detail="不可修改管理员 apikey")
    if old_row[0]["status"] != "active":
        raise HTTPException(status_code=400, detail="原 apikey 已删除,不可修改")

    new_exists = db.query("SELECT apikey FROM api_keys WHERE apikey=%s", (new_key,))
    if new_exists:
        raise HTTPException(status_code=409, detail="新 apikey 已存在")

    # 事务:换主键 + 计费记录迁移
    @db.transaction
    def _do(cur, exec) -> None:
        exec("UPDATE api_keys SET apikey=%s WHERE apikey=%s", (new_key, old_key))
        exec("UPDATE billing_records SET apikey=%s WHERE apikey=%s", (new_key, old_key))

    _do()

    # 方案组文件 owner 迁移(文件存储,不在 MySQL 事务内)
    _migrate_scheme_owner(old_key, new_key)
    return {"old_apikey": old_key, "new_apikey": new_key, "migrated": True}


def delete_apikey(apikey: str) -> dict:
    """删除 apikey(软删)。管理员不可删。"""
    row = db.query("SELECT * FROM api_keys WHERE apikey=%s", (apikey,))
    if not row:
        raise HTTPException(status_code=404, detail="apikey 不存在")
    if row[0]["role"] == "admin":
        raise HTTPException(status_code=403, detail="不可删除管理员 apikey")
    db.execute("UPDATE api_keys SET status='deleted' WHERE apikey=%s", (apikey,))
    return {"apikey": apikey, "deleted": True}


def _migrate_scheme_owner(old_key: str, new_key: str) -> None:
    """扫 data/schemes/*.json,把 owner=旧 key 的方案组改为新 key。"""
    data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "schemes"
    if not data_dir.exists():
        return
    import json

    for f in data_dir.glob("*.json"):
        try:
            group = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if group.get("owner") == old_key:
            group["owner"] = new_key
            f.write_text(json.dumps(group, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_admin() -> None:
    """启动时确保管理员 key 存在(.env ADMIN_APIKEY,额度 99999999)。幂等。"""
    from agents.sentiment_query_agent.auth import admin_apikey

    key = admin_apikey()
    if not key:
        raise RuntimeError("ADMIN_APIKEY 未配置(管理员 apikey 必填)")
    row = db.query("SELECT apikey FROM api_keys WHERE apikey=%s", (key,))
    if not row:
        db.execute(
            "INSERT INTO api_keys (apikey, role, status, free_quota, paid_quota) "
            "VALUES (%s, 'admin', 'active', %s, 0)",
            (key, _ADMIN_FREE_QUOTA),
        )
