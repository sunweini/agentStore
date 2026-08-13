"""法条灌库脚本:python -m agents.contract_review_agent.scripts.seed_laws

数据:agents/contract_review_agent/data/laws/*.md(人工从权威来源采集,
国家法律法规数据库 flk.npc.gov.cn / 全国人大官网,严禁 LLM 生成/记忆填充,
逐条记来源 URL + 采集日期,可人工核对)。

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md §4.2。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from agents.contract_review_agent.store.law_store import LawStore


def main(data_dir: Path, laws_dir: Path, if_empty: bool = False) -> None:
    store = LawStore(data_dir)
    if if_empty and store.vector_count() > 0:
        print(f"向量库非空({store.vector_count()} 条),--if-empty 跳过 seed")
        return
    for md_path in sorted(laws_dir.glob("*.md")):
        result = store.seed(md_path.read_text(encoding="utf-8"))
        print(f"{result['law_name']}: {result['count']} 条,errors={result['errors']}")
    print("\n== 精确索引摘要(list_laws,校验层 verify_ref 依据)==")
    for law in store.list_laws():
        print(f"  {law['law_name']} [domain={law['domain']}] {law['count']} 条")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=Path("data/contract-rag"))
    ap.add_argument("--laws-dir", type=Path,
                    default=Path("agents/contract_review_agent/data/laws"))
    ap.add_argument("--if-empty", action="store_true",
                    help="向量库非空时跳过灌库(幂等;部署脚本据此只在空库时灌一次)")
    args = ap.parse_args()
    main(args.data_dir, args.laws_dir, args.if_empty)
