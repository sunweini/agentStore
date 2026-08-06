"""节点函数:agent1 的图节点与路由。

设计见 docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md 第 6 节。

待实现:
- agent_node(state):加载 prompt,把 state["messages"] 发 LLM,返回结果
- should_continue(state):最后一条消息有 tool_calls → "tools",否则 END
  (官方标准路由模式)

引用: https://docs.langchain.com/oss/python/langgraph/quickstart#part-2-enhance-the-chatbot-with-tool-calling
"""
