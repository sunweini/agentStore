#!/usr/bin/env python3
"""步骤 2 主体画像:LLM 原始输出 → 标准画像 JSON。

格式契约见 references/output-formats.md 步骤 2。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import emit, fail, gap, load_input, norm_list, norm_str, with_gaps  # noqa: E402

ROLES = ("承包商", "业主", "ai判定")


def main() -> None:
    raw = load_input()
    profile = raw.get("profile", raw)

    role = norm_str(profile.get("role"), "role", required=True)
    if role not in ROLES:
        gap(f"角色 {role!r} 非法,补默认 ai判定")
        role = "ai判定"

    result = {
        "profile": {
            "role": role,
            "relevance_rules": {
                "direct": "点名监测主体自身或其在场承包的项目",
                "indirect": "未点名但可确定指向关联实体/业主(传导风险)",
                "context": "行业性报道,未指向具体公司",
            },
            "regions": norm_list(profile.get("regions"), "regions"),
        }
    }
    if role == "ai判定":
        gap("角色由 AI 判定,承包商视角时业主负面为传导风险")
    emit(with_gaps(result))


if __name__ == "__main__":
    main()
