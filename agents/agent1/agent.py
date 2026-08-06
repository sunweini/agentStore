"""图构建:agent1 的 LangGraph 定义。

职责:构建 StateGraph,注册节点/路由/工具,compile 返回图。
langgraph.json 注册入口: "./agents/agent1/agent.py:build_agent"

设计见 docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md 第 6 节。

图结构(循环型 ReAct):
  START → agent_node ──有 tool_calls──→ tools_node
              │                          │
              └──────无 tool_calls───────┘
                     → END

待实现:
- build_agent() -> CompiledStateGraph
- add_node("agent", agent_node)
- add_node("tools", ToolNode(tools))
- add_edge(START, "agent")
- add_conditional_edges("agent", should_continue, {...})
- add_edge("tools", "agent")

引用: https://docs.langchain.com/oss/python/langgraph/quickstart
"""
