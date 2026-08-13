"""contract-review-agent 骨架阶段测试:仅验证包可导入,不测行为。

项目开发铁律(骨架阶段只建目录+占位文档,不写实现代码):本测试仅做 import 冒烟;
解析 / 引用校验层 / 法条 seed / 计费等行为测试待实现阶段补充(设计 §9 测试策略)。
"""


def test_contract_agent_package_imports():
    """验证 contract_review_agent 包及核心子模块可导入(骨架占位)。"""
    from agents.contract_review_agent import agent, api  # noqa: F401
    from agents.contract_review_agent.graph import nodes, verify  # noqa: F401
    assert True
