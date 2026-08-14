#!/usr/bin/env python
"""存量计费迁移:老表 api_keys / billing_records → 统一新表 agent_*。

背景:公共计费组件(common/billing.py)用 (apikey, agent) 维度单表收敛。sentiment
已上生产(agentstore 库),老表 api_keys/billing_records 有存量数据需迁入
agent_api_keys / agent_billing_records(agent='sentiment',bill_no=group_id)。
contract 无生产数据,不迁(设计 §5)。

为什么 check-then-insert 而非 INSERT OR IGNORE:统一兼容 SQLite(测试)/MySQL(生产)
双后端,避免方言 SQL;迁移一次性 + 幂等重跑,先查重足够(设计 §5「或先查重」)。

接口:
    migrate(source_agent="sentiment", dry_run=True) -> dict
        dry_run=True  只统计源表行数,不写库(默认,设计「dry-run 默认先行」)。
        dry_run=False 迁入 + 迁移后校验(行数 + 每 apikey 额度四元组),失败抛错。

CLI(生产流程:先 --dry-run 验证,再 --apply 实迁):
    python scripts/migrate_billing.py --dry-run    # 只统计
    python scripts/migrate_billing.py --apply      # 实迁 + 校验
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 直接以脚本运行(CLI)时,脚本目录而非仓库根在 sys.path 上;把仓库根加上
# 才能 import common。作为模块被测试 import 时仓库根已在 sys.path,此行为无害。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import db

_QUOTA_COLS = ("free_quota", "free_used", "paid_quota", "paid_used")


def _existing_keys(agent: str) -> set[str]:
    """已迁入 agent_api_keys 的 apikey 集合((apikey, agent) 复合主键,按 agent 过滤)。"""
    return {r["apikey"] for r in db.query(
        "SELECT apikey FROM agent_api_keys WHERE agent=%s", (agent,))}


def _existing_bills(agent: str) -> set[str]:
    """已迁入 agent_billing_records 的 bill_no 集合(UNIQUE(agent, bill_no))。"""
    return {r["bill_no"] for r in db.query(
        "SELECT bill_no FROM agent_billing_records WHERE agent=%s", (agent,))}


def _validate(agent: str) -> None:
    """迁移后校验(幂等语义):每条老表行都有对应新表行且数据一致,不一致抛错。

    与"新表总行数 == 老表总行数"的区别(终审 M4):部署后 agent_api_keys /
    agent_billing_records 会有新增的普通用户/任务行(非本次迁移写入),全局计数
    相等必然误报"行数不等" —— 改为按源行逐条断言"存在且一致"(子集校验),
    新表额外行不在本次迁移目标内,不参与比对。老表保留不动(设计 §10),
    新表缺/错即中止,便于切表前人工核对。
    """
    for old in db.query(f"SELECT apikey, {','.join(_QUOTA_COLS)} FROM api_keys"):
        new = db.query(
            f"SELECT {','.join(_QUOTA_COLS)} FROM agent_api_keys "
            "WHERE apikey=%s AND agent=%s", (old["apikey"], agent))
        if not new:
            raise RuntimeError(
                f"迁移校验失败: agent_api_keys 缺 apikey={old['apikey']} agent={agent}")
        if tuple(new[0][c] for c in _QUOTA_COLS) != tuple(old[c] for c in _QUOTA_COLS):
            raise RuntimeError(
                f"迁移校验失败: apikey={old['apikey']} 额度四元组"
                f"(free_quota/free_used/paid_quota/paid_used) 不一致")
    for old in db.query("SELECT apikey, group_id, status, quota_type, "
                        "created_at, committed_at FROM billing_records"):
        new = db.query(
            "SELECT apikey, status, quota_type, created_at, committed_at "
            "FROM agent_billing_records WHERE agent=%s AND bill_no=%s",
            (agent, old["group_id"]))
        if not new:
            raise RuntimeError(
                f"迁移校验失败: agent_billing_records 缺 bill_no={old['group_id']} agent={agent}")
        row = new[0]
        for col in ("apikey", "status", "quota_type", "created_at", "committed_at"):
            if row[col] != old[col]:
                raise RuntimeError(
                    f"迁移校验失败: bill_no={old['group_id']} 字段 {col} 不一致"
                    f"(新 {row[col]!r} != 老 {old[col]!r})")


def migrate(source_agent: str = "sentiment", dry_run: bool = True) -> dict:
    """迁移 sentiment 老表到统一新表。返回统计 dict。

    keys/records = 源表行数(迁移范围);inserted_keys/inserted_records = 本次实际
    写入行数(dry_run 恒 0)。已存在的 (apikey, agent) / (agent, bill_no) 跳过 → 幂等。
    时间戳:老表 billing_records 有 created_at/committed_at 则一并迁入(保真),
    无(None)则落新表 DEFAULT(created_at 现时 / committed_at NULL)。
    """
    keys = db.query("SELECT apikey, role, status, "
                    "free_quota, free_used, paid_quota, paid_used FROM api_keys")
    records = db.query("SELECT apikey, group_id, status, quota_type, "
                       "created_at, committed_at FROM billing_records")
    inserted_keys = inserted_records = 0
    if not dry_run:
        existing_keys = _existing_keys(source_agent)
        for k in keys:
            if k["apikey"] in existing_keys:
                continue  # 幂等:该 (apikey, agent) 已迁入,跳过
            db.execute(
                "INSERT INTO agent_api_keys (apikey, agent, role, status, "
                "free_quota, paid_quota, free_used, paid_used) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (k["apikey"], source_agent, k["role"], k["status"], k["free_quota"],
                 k["paid_quota"], k["free_used"], k["paid_used"]))
            inserted_keys += 1
        existing_bills = _existing_bills(source_agent)
        for r in records:
            if r["group_id"] in existing_bills:
                continue  # 幂等:该 (agent, bill_no) 已迁入,跳过
            cols = ["apikey", "agent", "bill_no", "status", "quota_type"]
            vals = [r["apikey"], source_agent, r["group_id"], r["status"], r["quota_type"]]
            # 老表时间戳有则带(保真),无则落新表 DEFAULT(统一兼容 SQLite/MySQL)
            for col in ("created_at", "committed_at"):
                if r.get(col):
                    cols.append(col)
                    vals.append(r[col])
            db.execute(
                f"INSERT INTO agent_billing_records ({','.join(cols)}) "
                f"VALUES ({','.join(['%s'] * len(vals))})",
                tuple(vals))
            inserted_records += 1
        _validate(source_agent)
    return {
        "agent": source_agent,
        "dry_run": dry_run,
        "keys": len(keys),
        "records": len(records),
        "inserted_keys": inserted_keys,
        "inserted_records": inserted_records,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="存量计费迁移:api_keys/billing_records → agent_api_keys/agent_billing_records"
                    "(agent 维度,bill_no=group_id)")
    p.add_argument("--agent", default="sentiment",
                   help="源 agent(默认 sentiment;contract 无生产数据不迁)")
    p.add_argument("--dry-run", action="store_true",
                   help="只统计不写库(默认行为;生产先以本参数验证)")
    p.add_argument("--apply", action="store_true",
                   help="真正执行迁移 + 迁移后校验(缺省为 dry-run)")
    args = p.parse_args()
    dry_run = not args.apply
    try:
        stats = migrate(source_agent=args.agent, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 —— CLI 兜底,把校验失败等明确报给运维
        print(f"迁移失败: {exc}")
        return 1
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
