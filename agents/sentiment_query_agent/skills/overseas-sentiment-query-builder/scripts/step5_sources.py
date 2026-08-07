#!/usr/bin/env python3
"""步骤 5 属地信源:LLM 原始输出 → 每轨补 sources(域名白名单)。

格式契约见 references/output-formats.md 步骤 5。与步骤 4 schemes 结构一一对应。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import emit, gap, load_input, norm_list, norm_str, with_gaps  # noqa: E402


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
            sources = norm_list(tr.get("sources"), f"schemes[{i}].tracks[{j}].sources")
            # 域名标准化:去协议/去路径
            clean = []
            for s in sources:
                s = norm_str(s, f"schemes[{i}].tracks[{j}].sources")
                if not s:
                    continue
                s = s.split("//")[-1].split("/")[0].strip().lower()
                if s:
                    clean.append(s)
            if not clean:
                gap(f"方案 {sc.get('id', i)} 轨 {tr.get('key', j)} 信源为空,需 websearch 补属地媒体域名")
            normed_tracks.append({"key": tr.get("key", ""), "sources": clean})
        normed.append({"id": norm_str(sc.get("id"), f"schemes[{i}].id", default=f"Q{i}"),
                       "tracks": normed_tracks})
    emit(with_gaps({"schemes": normed}))


if __name__ == "__main__":
    main()
