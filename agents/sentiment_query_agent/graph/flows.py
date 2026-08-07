"""图构建:6 步流水线顺序图。

设计见 docs/superpowers/specs/2026-08-06-sentiment-query-agent-sentiment-query-agent-design.md §2/§4。

图结构:
  START → step1 → step2 → step3 → step4 → step5 → step6 → END

- 顺序边,6 步串行;每步产物经 state 传递。
- 单步失败:节点内捕获标 error,不中断图(步骤状态记录 error,可重跑)。
- 中断/续跑:AsyncSqliteSaver checkpointer,thread_id = group_id。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.sentiment_query_agent.graph.nodes import (
    step1_node, step2_node, step3_node, step4_node, step5_node, step6_node,
)
from agents.sentiment_query_agent.graph.state import AgentState

_NODES = [
    ("step1", step1_node),
    ("step2", step2_node),
    ("step3", step3_node),
    ("step4", step4_node),
    ("step5", step5_node),
    ("step6", step6_node),
]


def build_graph():
    """构建 6 步流水线图(compile 时挂 AsyncSqliteSaver)。"""
    g = StateGraph(AgentState)
    for name, node in _NODES:
        g.add_node(name, node)
    g.add_edge(START, "step1")
    for i in range(len(_NODES) - 1):
        g.add_edge(_NODES[i][0], _NODES[i + 1][0])
    g.add_edge(_NODES[-1][0], END)
    return g
