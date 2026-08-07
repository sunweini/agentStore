"""导出转换层:勾选后的方案组 → skill spec 格式(tasks/keywords/extra_notes)。

设计见 docs/superpowers/specs/2026-08-06-sentiment-query-agent-sentiment-query-agent-design.md §7。

把勾选的轨转成 spec 的 tasks 行(第 4+5+6 步产物拼图),关键词字典转 keywords 行,
GAP 转 extra_notes。再调 skill 的 build_task_xlsx.py 生成 Excel。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_SKILL_DIR = (
    Path(__file__).resolve().parent.parent
    / "skills" / "overseas-sentiment-query-builder"
)
_SCRIPT = _SKILL_DIR / "scripts" / "build_task_xlsx.py"


def group_to_spec(group: dict) -> dict:
    """方案组 → skill spec 格式。

    勾选语义:方案 selected 且轨 selected 才进 tasks(与原型"勾选轨数=任务行数"一致)。
    """
    tasks = []
    for sc in group.get("schemes", []):
        if not sc.get("selected", False):
            continue
        for tr in sc.get("tracks", []):
            if not tr.get("selected", False):
                continue
            tasks.append({
                "id": f"{sc['id']}-{tr['key']}",
                "group": sc.get("name", ""),
                "region": sc.get("region", ""),
                "lang": sc.get("lang", ""),
                "boolean": tr.get("boolean_query", ""),
                "google": tr.get("google_query", ""),
                "sources": tr.get("sources", []),
                "frequency": tr.get("frequency", "周级"),
                "risk": tr.get("risk", "medium"),
                "relevance": tr.get("relevance", "direct"),
                "status": "待启用",
                "note": sc.get("desc", ""),
            })
    # 缺字段 GAP → extra_notes
    extra_notes = []
    gap_no = 1
    for sc in group.get("schemes", []):
        for gap in sc.get("gaps", []):
            extra_notes.append({"key": f"GAP{gap_no:03d}", "value": gap})
            gap_no += 1
    if not extra_notes:
        extra_notes.append({"key": "待补缺口", "value": "无"})

    spec = {
        "title": f"{group.get('company_name', '')}舆情检索任务清单 · 使用说明",
        "tasks": tasks,
        "keywords": group.get("keywords", []),
        "extra_notes": extra_notes,
    }
    return spec


def export_excel(group: dict, out_path: str) -> str:
    """勾选后的方案组 → Excel(调 skill 的 build_task_xlsx.py)。

    Args:
        group: 已 commit 的方案组。
        out_path: 输出 .xlsx 路径。

    Returns:
        生成的文件路径。
    """
    if not _SCRIPT.exists():
        raise RuntimeError(f"skill 脚本缺失: {_SCRIPT}")
    spec = group_to_spec(group)
    spec_path = Path(out_path).with_suffix(".spec.json")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    proc = subprocess.run(
        ["python3", str(_SCRIPT), str(spec_path), out_path],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"build_task_xlsx.py 失败: {proc.stderr[:500]}")
    return out_path
