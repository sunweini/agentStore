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


def main(data_dir: Path, laws_dir: Path) -> None:
    store = LawStore(data_dir)
    for md_path in sorted(laws_dir.glob("*.md")):
        result = store.seed(md_path.read_text(encoding="utf-8"))
        print(f"{result['law_name']}: {result['count']} 条,errors={result['errors']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=Path("data/contract-rag"))
    ap.add_argument("--laws-dir", type=Path,
                    default=Path("agents/contract_review_agent/data/laws"))
    args = ap.parse_args()
    main(args.data_dir, args.laws_dir)
