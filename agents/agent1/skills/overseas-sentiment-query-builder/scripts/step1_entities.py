#!/usr/bin/env python3
"""步骤 1 实体测绘:LLM 原始输出 → 标准实体簇 JSON。

格式契约见 references/output-formats.md 步骤 1。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import emit, fail, gap, load_input, norm_list, norm_str, with_gaps  # noqa: E402


def main() -> None:
    raw = load_input()
    entities = raw.get("entities", raw)  # 兼容直接给 entities 或包一层

    overseas = norm_list(entities.get("overseas_entities"), "overseas_entities")
    normed_overseas = []
    for i, e in enumerate(overseas):
        if not isinstance(e, dict):
            gap("海外法人第 %d 项非对象,跳过", i)
            continue
        name = norm_str(e.get("name"), f"overseas_entities[{i}].name", required=True)
        normed_overseas.append({
            "name": name,
            "lang": norm_str(e.get("lang"), f"overseas_entities[{i}].lang", default="en"),
            "region": norm_str(e.get("region"), f"overseas_entities[{i}].region"),
        })

    result = {
        "entities": {
            "parent": norm_str(entities.get("parent"), "parent"),
            "subsidiaries": norm_list(entities.get("subsidiaries"), "subsidiaries"),
            "overseas_entities": normed_overseas,
            "spelling_variants": norm_list(entities.get("spelling_variants"), "spelling_variants"),
            "interference_sources": norm_list(entities.get("interference_sources"), "interference_sources"),
        }
    }
    if not result["entities"]["parent"]:
        gap("母公司名缺失(只输入了公司名,需 websearch 验证)")
    emit(with_gaps(result))


if __name__ == "__main__":
    main()
