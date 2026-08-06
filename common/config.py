"""配置加载。

职责:统一从 .env / 环境变量读取配置,密钥不硬编码。

设计见 docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md 第 4/9 节。

.env 变量约定:
  LLM_PROVIDER=deepseek          当前默认供应商
  LLM_MODEL=deepseek-chat        当前默认模型
  DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
  OPENAI_API_KEY / OPENAI_MODEL
  ANTHROPIC_API_KEY / ANTHROPIC_MODEL
"""
