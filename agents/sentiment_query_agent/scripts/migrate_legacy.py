#!/usr/bin/env python3
"""数据迁移:JSON 计费/方案组 → MySQL 配额与资费。

设计见 docs/superpowers/specs/2026-08-11-quota-billing-stats-design.md §6。

迁移内容:
1. 旧计费 JSON(data/billing/<user>.json)→ billing_records(MySQL)
   - 文件按用户标识命名;API_KEYS_JSON 映射用户标识 → apikey
   - pending/committed 记录写入;committed 扣减对应额度(先 free 后 paid)
2. 方案组 owner:用户标识 → apikey(扫 data/schemes/*.json)
3. 初始化 api_keys:现有 apikey → MySQL(免费 10/付费 0;管理员 99999999)

用法:
    DB_BACKEND=mysql MYSQL_URL=... ADMIN_APIKEY=sk-demo-hefangyuan20260810 \
    python3 agents/sentiment_query_agent/scripts/migrate_legacy.py [--dry-run]

--dry-run:只报告不写库(默认)。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 允许从仓库根直接运行(scripts/ → agents/ → sentiment_query_agent/ → agents/ → 根)
# 容器内:scripts/ → sentiment_query_agent/ → agents/ → 根(/app)
import os

_root_candidates = [
    Path(__file__).resolve().parent.parent.parent.parent.parent,   # 本地仓库根
    Path(__file__).resolve().parent.parent.parent.parent,          # 容器内 /app(脚本在 agents/xxx/scripts/)
    Path(os.getcwd()),                                             # 当前目录(直接 cd 到根跑)
]
for _c in _root_candidates:
    if (_c / "common").is_dir():
        sys.path.insert(0, str(_c))
        _PROJECT_ROOT = _c
        break
else:
    _PROJECT_ROOT = _root_candidates[0]
    sys.path.insert(0, str(_PROJECT_ROOT))

from common import config, db  # noqa: E402

# 数据目录:优先 DATA_DIR 环境变量(容器内 /app/data),否则项目根 data/
_DATA_DIR = Path(config.get_env("DATA_DIR", str(_PROJECT_ROOT / "data")))
_DRY_RUN = "--apply" not in sys.argv  # 默认 dry-run;传 --apply 才实际写库


def _legacy_apikey_map() -> dict:
    """旧 API_KEYS_JSON:用户标识 → apikey 反查(apikey → 用户标识 的逆向)。"""
    raw = config.get_env("API_KEYS_JSON", "")
    if not raw:
        return {}
    try:
        mapping = json.loads(raw)  # {apikey: user}
        return {v: k for k, v in mapping.items()}  # {user: apikey}
    except json.JSONDecodeError:
        return {}


def _migrate_billing(apikey_of: dict) -> None:
    """旧计费 JSON → billing_records + 额度扣减。"""
    billing_dir = _DATA_DIR / "billing"
    if not billing_dir.exists():
        print("[skip] data/billing 不存在")
        return
    for f in sorted(billing_dir.glob("*.json")):
        user = f.stem  # 文件名 = 用户标识
        apikey = apikey_of.get(user)
        if not apikey:
            print(f"[skip] {f.name}: 用户标识 {user} 无对应 apikey(API_KEYS_JSON 未配置)")
            continue
        try:
            records = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"[warn] {f.name}: 解析失败,跳过")
            continue
        for r in records:
            group_id = r["group_id"]
            status = r["status"]
            if status not in ("pending", "committed"):
                print(f"[skip] {f.name} {group_id}: 状态 {status} 不迁移")
                continue
            exists = db.query(
                "SELECT id FROM billing_records WHERE group_id=%s", (group_id,)
            )
            if exists:
                print(f"[skip] {group_id}: 已存在")
                continue
            if _DRY_RUN:
                print(f"[dry] {group_id}: {status} → billing_records({apikey})")
                continue
            if status == "pending":
                db.execute(
                    "INSERT INTO billing_records (apikey, group_id, status) "
                    "VALUES (%s, %s, 'pending')",
                    (apikey, group_id),
                )
            else:  # committed:扣额度
                _commit_with_quota(apikey, group_id)
            print(f"[ok] {group_id}: {status} 迁移")


def _commit_with_quota(apikey: str, group_id: str) -> None:
    """committed 记录:写 billing_records + 扣额度(先 free 后 paid)。"""

    @db.transaction
    def _do(cur, exec) -> None:
        rows = exec("SELECT * FROM api_keys WHERE apikey=%s FOR UPDATE", (apikey,))
        key = rows[0] if rows else None
        if key is None:
            raise RuntimeError(f"apikey {apikey} 不存在,先初始化 api_keys")
        if key["free_used"] < key["free_quota"]:
            exec("UPDATE api_keys SET free_used=free_used+1 WHERE apikey=%s", (apikey,))
            quota_type = "free"
        else:
            exec("UPDATE api_keys SET paid_used=paid_used+1 WHERE apikey=%s", (apikey,))
            quota_type = "paid"
        exec(
            "INSERT INTO billing_records (apikey, group_id, status, quota_type, committed_at) "
            "VALUES (%s, %s, 'committed', %s, NOW())",
            (apikey, group_id, quota_type),
        )

    _do()


def _migrate_scheme_owner(apikey_of: dict) -> None:
    """方案组 owner:用户标识 → apikey。"""
    schemes_dir = _DATA_DIR / "schemes"
    if not schemes_dir.exists():
        print("[skip] data/schemes 不存在")
        return
    for f in sorted(schemes_dir.glob("*.json")):
        if f.name == "index.json":
            continue
        try:
            group = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        owner = group.get("owner", "")
        apikey = apikey_of.get(owner)
        if apikey and owner != apikey:
            if _DRY_RUN:
                print(f"[dry] {f.name}: owner {owner} → {apikey}")
                continue
            group["owner"] = apikey
            f.write_text(json.dumps(group, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[ok] {f.name}: owner 迁移")


def _init_apikeys(apikey_of: dict) -> None:
    """初始化 api_keys:旧 apikey → MySQL(免费 10/付费 0)+ 管理员。"""
    admin_key = config.get_env("ADMIN_APIKEY", "")
    keys = set(apikey_of.values()) | ({admin_key} if admin_key else set())
    for key in sorted(keys):
        exists = db.query("SELECT apikey FROM api_keys WHERE apikey=%s", (key,))
        if exists:
            print(f"[skip] {key}: 已存在")
            continue
        role = "admin" if key == admin_key else "normal"
        free = 99999999 if role == "admin" else 10
        if _DRY_RUN:
            print(f"[dry] api_keys: {key} ({role}, free={free})")
            continue
        db.execute(
            "INSERT INTO api_keys (apikey, role, status, free_quota, paid_quota) "
            "VALUES (%s, %s, 'active', %s, 0)",
            (key, role, free),
        )
        print(f"[ok] api_keys: {key} ({role}, free={free})")


def main() -> None:
    print(f"迁移模式: {'DRY-RUN(不写库)' if _DRY_RUN else '实际执行'}")
    # 连接可用性
    try:
        db.query("SELECT 1 AS ok")
    except RuntimeError as exc:
        print(f"[error] 数据库连接失败: {exc}")
        sys.exit(1)

    apikey_of = _legacy_apikey_map()
    print(f"API_KEYS_JSON 映射: {len(apikey_of)} 个用户标识")

    _init_apikeys(apikey_of)
    _migrate_billing(apikey_of)
    _migrate_scheme_owner(apikey_of)
    print("迁移完成(DRY-RUN 未写库)" if _DRY_RUN else "迁移完成")


if __name__ == "__main__":
    main()
