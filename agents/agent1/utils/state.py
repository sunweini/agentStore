"""状态定义:agent1 的图状态。

设计见 docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md 第 6 节。

待实现:
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # 对话/工具调用历史,自动追加
    task: str
    result: str

引用: https://docs.langchain.com/oss/python/langgraph/quickstart#state
"""
