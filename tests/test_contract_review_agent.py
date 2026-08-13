"""contract-review-agent 骨架阶段测试:仅验证包可导入,不测行为。

项目开发铁律(骨架阶段只建目录+占位文档,不写实现代码):本测试仅做 import 冒烟;
解析 / 引用校验层 / 法条 seed / 计费等行为测试待实现阶段补充(设计 §9 测试策略)。
"""


def test_contract_agent_package_imports():
    """验证 contract_review_agent 包及核心子模块可导入(骨架占位)。"""
    from agents.contract_review_agent import agent, api  # noqa: F401
    from agents.contract_review_agent.graph import nodes, verify  # noqa: F401
    assert True


from agents.contract_review_agent.utils.law_parser import parse_law_md, DOMAIN_ALIASES


def test_parse_law_md_basic():
    md = """# 测试法\n来源: https://example.com/x\n采集日期: 2026-08-13\n领域: labor\n\n## 第一条\n甲。\n\n## 第二条\n乙。"""
    articles, meta = parse_law_md(md)
    assert meta["law_name"] == "测试法"
    assert meta["domain"] == "labor"
    assert len(articles) == 2
    assert articles[0].article_no == "第一条"
    assert articles[0].text == "甲。"
    assert articles[0].law_name == "测试法"
    assert articles[0].source_url == "https://example.com/x"
    assert articles[0].domain == "labor"


def test_parse_law_md_skips_malformed_article():
    md = "# 测试法\n来源: u\n\n## 无编号\n孤儿文本\n\n## 第三条\n正常。"
    articles, meta = parse_law_md(md)
    assert [a.article_no for a in articles] == ["第三条"]
    assert meta["errors"]


def test_domain_aliases_contract_type():
    assert DOMAIN_ALIASES["劳动合同"] == "labor"
    assert DOMAIN_ALIASES["买卖"] == "contract"
