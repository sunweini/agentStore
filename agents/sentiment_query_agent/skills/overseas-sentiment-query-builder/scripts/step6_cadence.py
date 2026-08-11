#!/usr/bin/env python3
"""步骤 6 频次定级:LLM 原始输出 → 每轨补 frequency/risk/relevance,组装完整 task 行。

格式契约见 references/output-formats.md 步骤 6。对齐 spec tasks[] 行(完整)。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import FREQUENCIES, RELEVANCES, RISKS, emit, gap, load_input, norm_choice, norm_list, norm_str, with_gaps  # noqa: E402


def main() -> None:
    raw = load_input()
    schemes = norm_list(raw.get("schemes"), "schemes", required=True)
    normed = []
    for i, sc in enumerate(schemes):
        if not isinstance(sc, dict):
            gap("方案第 %d 项非对象,跳过", i)
            continue
        tracks = norm_list(sc.get("tracks"), f"schemes[{i}].tracks", required=True)
        normed_tracks = []
        for j, tr in enumerate(tracks):
            if not isinstance(tr, dict):
                gap("方案 %d 轨第 %d 项非对象,跳过", i, j)
                continue
            freq = norm_choice(tr.get("frequency"), f"schemes[{i}].tracks[{j}].frequency",
                               FREQUENCIES, "周级")
            risk = norm_choice(tr.get("risk"), f"schemes[{i}].tracks[{j}].risk",
                               RISKS, "medium")
            rel = norm_choice(tr.get("relevance"), f"schemes[{i}].tracks[{j}].relevance",
                              RELEVANCES, "direct")
            # 快讯轨强制快讯/小时级
            if tr.get("key") == "快讯" and freq != "快讯/小时级":
                gap(f"快讯轨 {sc.get('id', i)} 频次应为快讯/小时级,已纠正")
                freq = "快讯/小时级"
            normed_tracks.append({"key": tr.get("key", ""), "frequency": freq,
                                  "risk": risk, "relevance": rel})
        normed.append({"id": norm_str(sc.get("id"), f"schemes[{i}].id", default=f"Q{i}"),
                       "tracks": normed_tracks})
    emit(with_gaps({"schemes": normed}))


if __name__ == "__main__":
    main()
