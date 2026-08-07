#!/usr/bin/env python3
"""步骤 4 双轨检索式:LLM 原始输出 → 标准 schemes 行(布尔+Google)。

格式契约见 references/output-formats.md 步骤 4。对齐 spec tasks[] 行(部分)。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import TRACK_KEYS, emit, fail, gap, load_input, norm_list, norm_str, with_gaps  # noqa: E402


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
            key = norm_str(tr.get("key"), f"schemes[{i}].tracks[{j}].key", required=True)
            if key not in TRACK_KEYS:
                gap(f"轨 key {key!r} 非法,跳过")
                continue
            boolean = norm_str(tr.get("boolean"), f"schemes[{i}].tracks[{j}].boolean", required=True)
            google = norm_str(tr.get("google"), f"schemes[{i}].tracks[{j}].google", required=True)
            normed_tracks.append({
                "key": key,
                "boolean_query": boolean,
                "google_query": google,
                "sources": [],
                "frequency": "",
                "relevance": "",
                "selected": True,
            })
        if not normed_tracks:
            gap(f"方案 {sc.get('id', i)} 无有效轨,跳过")
            continue
        normed.append({
            "id": norm_str(sc.get("id"), f"schemes[{i}].id", default=f"Q{i}"),
            "name": norm_str(sc.get("name"), f"schemes[{i}].name"),
            "region": norm_str(sc.get("region"), f"schemes[{i}].region"),
            "lang": norm_str(sc.get("lang"), f"schemes[{i}].lang"),
            "desc": norm_str(sc.get("desc"), f"schemes[{i}].desc"),
            "gaps": norm_list(sc.get("gaps"), f"schemes[{i}].gaps"),
            "tracks": normed_tracks,
            "selected": True,
        })
    if not normed:
        fail("schemes 为空或全部无有效轨:LLM 未按格式输出轨(检查 key 是否在 全量新闻/负面新闻/行业新闻/快讯/司法/招标)")
    emit(with_gaps({"schemes": normed}))


if __name__ == "__main__":
    main()
