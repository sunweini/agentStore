"""状态定义:agent1 的图状态。

设计见 docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md 第 6 节。

messages 用 add_messages reducer:每次 node 返回消息自动追加到历史,
不需要手动合并。这是 LangGraph 官方推荐写法。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """agent1 图状态。

    - messages: 对话/工具调用历史,add_messages 自动追加。
    - task: 用户任务描述。
    - result: 最终结果(图结束时填充)。
    """

    messages: Annotated[list[AnyMessage], add_messages]
    task: str
    result: str
