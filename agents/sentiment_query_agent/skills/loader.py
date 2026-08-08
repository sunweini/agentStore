"""load_skill 工具:skill 渐进式披露加载(官方 skill 架构)。

设计见 docs/superpowers/specs/2026-08-06-sentiment-query-agent-sentiment-query-agent-design.md §6。
官方文档: /oss/python/langchain/multi-agent/skills

- agent 启动只加载 skill 摘要(description)。
- agent 需要时调 load_skill 取完整内容(SKILL.md + references)。
- 查找顺序:agents/<agent>/skills/ → common/skills/。
- 与 stepN.py 脚本职责分离:load_skill 取方法论知识(喂 LLM),stepN.py 做格式契约。
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import tool

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_AGENT_SKILLS = _PROJECT_ROOT / "agents" / "sentiment_query_agent" / "skills"
_COMMON_SKILLS = _PROJECT_ROOT / "common" / "skills"

# 本 agent 可用的 skill 摘要(启动即加载,渐进式披露的"摘要层")
_AVAILABLE_SKILLS = {
    "overseas-sentiment-query-builder": (
        "把企业海外舆情监测需求,变成可直接执行的检索任务清单——"
        "分层关键词字典、分组双轨检索式(布尔+Google)、属地信源白名单、频次定级。"
        "6 步流水线:实体测绘→主体画像→关键词字典→双轨检索式→属地信源→频次定级。"
        "分步脚本在 scripts/,格式契约在 references/output-formats.md。"
    ),
}


def _find_skill(name: str) -> Path | None:
    """按 agent → common 顺序查找 skill 目录。"""
    for root in (_AGENT_SKILLS, _COMMON_SKILLS):
        p = root / name
        if p.is_dir():
            return p
    return None


@tool
def load_skill(skill_name: str) -> str:
    """按需加载 skill 完整内容(SKILL.md + references 文件列表)。

    Args:
        skill_name: skill 名(overseas-sentiment-query-builder)。
    """
    if skill_name not in _AVAILABLE_SKILLS:
        return json.dumps(
            {"error": f"skill {skill_name} 不可用", "available": sorted(_AVAILABLE_SKILLS)},
            ensure_ascii=False,
        )
    skill_dir = _find_skill(skill_name)
    if skill_dir is None:
        return json.dumps({"error": f"skill {skill_name} 目录缺失"}, ensure_ascii=False)

    skill_md = skill_dir / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8") if skill_md.exists() else "(SKILL.md 缺失)"
    refs = sorted(p.name for p in (skill_dir / "references").glob("*.md"))
    scripts = sorted(p.name for p in (skill_dir / "scripts").glob("*.py"))
    return json.dumps(
        {"skill": skill_name, "summary": _AVAILABLE_SKILLS[skill_name],
         "references": refs, "scripts": scripts, "content": content},
        ensure_ascii=False,
    )


def skill_summary() -> str:
    """skill 摘要(注入 agent 系统提示,渐进式披露的摘要层)。"""
    return json.dumps(_AVAILABLE_SKILLS, ensure_ascii=False)
