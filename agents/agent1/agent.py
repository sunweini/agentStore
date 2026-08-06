"""图构建:agent1 的 LangGraph 定义。

设计见 docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md 第 6 节。

图结构(循环型 ReAct):
  START → agent_node ──有 tool_calls──→ tools_node
              │                          │
              └──────无 tool_calls───────┘
                     → END

- agent_node:LLM 决策(agents/agent1/utils/nodes.py)。
- tools_node:官方 prebuilt ToolNode,自动执行 LLM 请求的工具,结果回传。
- should_continue:路由,有 tool_calls 回 tools,否则 END。
- recursion_limit:运行时 config 参数(官方 graph-api 文档),invoke 时传入,
  防死循环: graph.invoke(inputs, config={"recursion_limit": 25})。

langgraph.json 注册入口: "./agents/agent1/agent.py:build_agent"
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from agents.agent1.utils.nodes import agent_node, should_continue
from agents.agent1.utils.state import AgentState
from agents.agent1.utils.tools import TOOLS


def build_agent():
    """构建并编译 agent1 的 LangGraph。

    递归上限(recursion_limit)是运行时 config 参数,不在这里设置,
    invoke 时传入: graph.invoke(inputs, config={"recursion_limit": 25})。

    Returns:
        编译后的 StateGraph(可直接 invoke)。
    """
    graph = StateGraph(AgentState)

    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(TOOLS))

    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", END: END},
    )
    graph.add_edge("tools", "agent")  # 工具结果回传,形成循环

    return graph.compile()
