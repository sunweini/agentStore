"""Prompt 加载:统一加载 agents/<agent>/prompts/<name>.md,返回 ChatPromptTemplate。

设计见 docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md 第 5 节。

能力:prompt 分离为可选能力,非强制。
- 默认一个 agent 一个 system.md
- 复杂 agent 可加 planner.md / executor.md 等,node 各用各的

模板语法:.md 内用 {var} 引用变量,node 调用 format_messages({"var": ...}) 填充。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate

# 项目根目录。prompt 文件按 agents/<agent>/prompts/<name>.md 存放。
_PROMPTS_ROOT = Path(__file__).resolve().parent.parent / "agents"


def load_prompt(agent: str, name: str = "system") -> ChatPromptTemplate:
    """加载 agents/<agent>/prompts/<name>.md,返回 ChatPromptTemplate。

    Args:
        agent: agent 目录名(如 "agent1")。
        name: prompt 文件名(不含扩展名),默认 "system"。

    Returns:
        ChatPromptTemplate(单 system 消息)。模板内 {var} 用
        format_messages({"var": value}) 填充。

    Raises:
        FileNotFoundError: prompt 文件不存在。
    """
    prompt_file = _PROMPTS_ROOT / agent / "prompts" / f"{name}.md"
    if not prompt_file.exists():
        raise FileNotFoundError(
            f"prompt 文件不存在: {prompt_file} "
            f"(期望位置 agents/{agent}/prompts/{name}.md)"
        )
    # from_messages 与模板格式一致;system 单消息足够 agent1 用。
    content = prompt_file.read_text(encoding="utf-8").strip()
    return ChatPromptTemplate.from_messages([("system", content)])
