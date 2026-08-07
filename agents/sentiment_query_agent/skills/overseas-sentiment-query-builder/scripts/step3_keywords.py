#!/usr/bin/env python3
"""步骤 3 分层关键词字典:LLM 原始输出 → 标准 keywords 行。

格式契约见 references/output-formats.md 步骤 3。对齐 spec keywords[] 行。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import LAYERS, emit, gap, load_input, norm_list, norm_str, with_gaps  # noqa: E402


def main() -> None:
    raw = load_input()
    keywords = norm_list(raw.get("keywords"), "keywords", required=True)
    normed = []
    for i, k in enumerate(keywords):
        if not isinstance(k, dict):
            gap("关键词第 %d 项非对象,跳过", i)
            continue
        layer = norm_str(k.get("layer"), f"keywords[{i}].layer", required=True)
        if layer not in LAYERS:
            gap(f"关键词第 {i} 项层 {layer!r} 非法,跳过")
            continue
        terms = norm_str(k.get("terms"), f"keywords[{i}].terms", required=True)
        guard = norm_str(k.get("guard"), f"keywords[{i}].guard")
        # ≤5 字符缩写必须带 context_guard
        short = [t.strip('"') for t in terms.replace(" ", ",").split(",") if 0 < len(t.strip('"')) <= 5]
        if short and not guard:
            gap(f"关键词 {terms!r} 含短缩写 {short},缺 context_guard")
        normed.append({
            "layer": layer,
            "category": norm_str(k.get("category"), f"keywords[{i}].category"),
            "terms": terms,
            "lang": norm_str(k.get("lang"), f"keywords[{i}].lang", default="全"),
            "guard": guard,
            "note": norm_str(k.get("note"), f"keywords[{i}].note"),
        })
    if not normed:
        gap("关键词字典为空,需 websearch 补充")
    emit(with_gaps({"keywords": normed}))


if __name__ == "__main__":
    main()
