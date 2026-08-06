"""多供应商模型工厂。

职责:按供应商注册表初始化 ChatModel,支持多供应商、多模型 ID。

设计见 docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md 第 4 节。

待实现:
- provider 注册表 {provider: builder},DeepSeek 经 ChatOpenAI(base_url=...) 接入
- get_chat_model(provider=None, model_id=None) -> BaseChatModel
  - provider 缺省读 env LLM_PROVIDER
  - model_id 缺省读对应供应商默认模型(如 DEEPSEEK_MODEL)

引用: https://docs.langchain.com/oss/python/integrations/chat/openai
"""
