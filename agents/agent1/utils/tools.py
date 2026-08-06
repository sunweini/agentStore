"""工具定义:agent1 专属工具。

设计见 docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md 第 7 节。

第一版:2 个占位工具,验证「LLM → 工具调用 → 回传 → 再决策」闭环,
不接真实业务。后续替换真实 API 只改工具函数内部,图结构不动。

待实现(用 LangChain @tool 装饰器):
- search_material(material_code)  # 按物料编码查库存,返回 JSON
- check_stock(warehouse)          # 查指定仓库实时库存

引用: https://docs.langchain.com/oss/python/langchain/tools
"""
