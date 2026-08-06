"""agent1 测试占位。

设计见 docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md 第 10 节。

实现后补三层测试:
1. 工具单测:直接调工具函数,断言返回。
2. 图单测:mock LLM,断言图按预期走节点/路由。
3. 端到端:真实调 DeepSeek(有 key 时),跑通完整流程。
"""
