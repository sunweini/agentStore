"""Prompt 加载工具。

职责:统一加载 agents/<agent>/prompts/<name>.md,返回 ChatPromptTemplate。

设计见 docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md 第 5 节。

能力:prompt 分离为可选能力,非强制。
- 默认一个 agent 一个 system.md
- 复杂 agent 可加 planner.md / executor.md 等,node 各用各的

待实现:
- load_prompt(agent, name="system") -> ChatPromptTemplate
- 模板内 {var} 引用变量,node 调用 format_messages 填充

引用: https://reference.langchain.com/python/langchain-core/prompts/chat/ChatPromptTemplate
"""
