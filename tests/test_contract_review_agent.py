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


# ---- 法条库(LawStore):Chroma 向量检索 + 源文件精确核验(设计 §4.2) ----


from pathlib import Path  # noqa: E402

from agents.contract_review_agent.store.law_store import LawStore  # noqa: E402

MD = """# 测试劳动合同法
来源: https://example.com/law
采集日期: 2026-08-13
领域: labor

## 第一条
用人单位应当依法支付劳动报酬。

## 第二十条
违约金不得超过实际损失。"""


def _store(tmp_path: Path) -> LawStore:
    s = LawStore(data_dir=tmp_path / "rag")
    s.seed(MD)
    return s


def test_seed_and_list(tmp_path):
    s = _store(tmp_path)
    laws = s.list_laws()
    assert laws[0]["law_name"] == "测试劳动合同法"
    assert laws[0]["count"] == 2
    assert laws[0]["domain"] == "labor"


def test_retrieve_domain_filter(tmp_path):
    s = _store(tmp_path)
    hits = s.retrieve("违约金过高", "劳动合同", k=3)
    assert hits, "应命中违约金条款"
    assert any("违约金" in h["text"] for h in hits)


def test_retrieve_no_filter_when_unknown_type(tmp_path):
    s = _store(tmp_path)
    hits = s.retrieve("违约金", "未知类型", k=3)
    assert hits


def test_verify_ref_exact(tmp_path):
    s = _store(tmp_path)
    assert s.verify_ref("测试劳动合同法", "第一条") == "用人单位应当依法支付劳动报酬。"
    assert s.verify_ref("测试劳动合同法", "不存在的条") is None


# ---- Task 4 文件解析层:章节构建 + 文件解析(设计 §4.1) ----

from pathlib import Path  # noqa: E402
import tempfile  # noqa: E402
import pytest  # noqa: E402
from agents.contract_review_agent.utils.chapterizer import build_chapters  # noqa: E402
from agents.contract_review_agent.utils.document_parser import (  # noqa: E402
    parse_document, Document, UnsupportedTypeError, ContractTooLongError)


def test_build_chapters_basic():
    chapters = build_chapters([
        ("第一章 总则", 1), ("本合同适用中国法律。", 0),
        ("第二章 价款", 1), ("价款为人民币拾万元。", 0), ("按月支付。", 0),
    ])
    assert [c.title for c in chapters] == ["第一章 总则", "第二章 价款"]
    assert "本合同适用中国法律。" in chapters[0].text
    assert "按月支付。" in chapters[1].text
    assert chapters[0].order == 1 and chapters[1].order == 2


def test_build_chapters_flat_fallback():
    chapters = build_chapters([("只有正文,没有标题。", 0), ("继续。", 0)])
    assert len(chapters) == 1
    assert chapters[0].title == "未命名章节"


def test_parse_unsupported_type():
    with pytest.raises(UnsupportedTypeError):
        parse_document("a.txt")


def test_parse_too_large():
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
        f.write(b"\x00" * 3000)
        p = f.name
    with pytest.raises((ContractTooLongError, Exception)):
        parse_document(p, max_bytes=1024)


# ---- Task 5 百度 OCR 云端客户端:mock HTTP,不真调百度(设计 §4.1) ----

from unittest.mock import patch  # noqa: E402
from agents.contract_review_agent.utils import ocr_client  # noqa: E402


def test_get_baidu_token():
    with patch("httpx.post") as m:
        m.return_value.status_code = 200
        m.return_value.json.return_value = {"access_token": "tok123"}
        assert ocr_client.get_baidu_token("ak", "sk") == "tok123"


def test_ocr_image_bytes_joins_lines():
    with patch("httpx.post") as m:
        m.return_value.status_code = 200
        m.return_value.json.return_value = {
            "words_result": [{"words": "第一条"}, {"words": "甲方应付款。"}]}
        assert ocr_client.ocr_image_bytes(b"img", "tok") == "第一条 甲方应付款。"


# ---- Task 6 章节审核节点:mock LLM 返回固定 JSON,验证节点装配(设计 §4.3) ----

from unittest.mock import MagicMock  # noqa: E402
from agents.contract_review_agent.graph.state import ChapterReview, Finding, LegalRef  # noqa: E402
from agents.contract_review_agent.graph.nodes import review_chapter  # noqa: E402


def test_review_chapter_with_mock_llm():
    fake = MagicMock()
    fake.invoke.return_value.content = (
        '{"chapter": "第一章", "findings": [{"原文引用": "违约赔 5%", '
        '"风险类型": "合规", "问题描述": "可能超限", "改进建议": "调低", '
        '"法律依据": [], "confidence": "suggestion"}]}')
    review = review_chapter(fake, contract_type="劳动合同", chapter={
        "title": "第一章", "text": "违约赔 5%。"}, review_prompt="请审核")
    assert review["chapter"] == "第一章"
    assert review["findings"][0]["confidence"] == "suggestion"


# ---- Task 7 引用校验层:逐条核验法律依据可回溯源文件原文(设计 §4.4,核心反幻觉) ----

from agents.contract_review_agent.graph.verify import verify_reviews  # noqa: E402

# 注:MD 常量名用 VERIFY_MD 避免覆盖本文件上方供 LawStore 其它测试使用的 MD
VERIFY_MD = """# 测试法
来源: u
采集日期: 2026-08-13
领域: labor

## 第一条
违约金不得超过实际损失的百分之三十。"""


def _verify_law(tmp_path: Path) -> LawStore:
    s = LawStore(data_dir=tmp_path / "rag")
    s.seed(VERIFY_MD)
    return s


def test_verify_keeps_exact_ref(tmp_path):
    reviews = [{"chapter": "A", "findings": [{
        "原文引用": "赔 5%", "风险类型": "合规", "问题描述": "x", "改进建议": "y",
        "法律依据": [{"law_name": "测试法", "article_no": "第一条",
                       "article_text": "违约金不得超过实际损失的百分之三十。"}],
        "confidence": "statutory"}]}]
    out = verify_reviews(reviews, _verify_law(tmp_path))
    assert out[0]["findings"][0]["confidence"] == "statutory"
    assert len(out[0]["findings"][0]["法律依据"]) == 1


def test_verify_drops_nonexistent_article(tmp_path):
    reviews = [{"chapter": "A", "findings": [{
        "原文引用": "q", "风险类型": "漏洞", "问题描述": "x", "改进建议": "y",
        "法律依据": [{"law_name": "测试法", "article_no": "第九百条",
                       "article_text": "编造的。"}],
        "confidence": "statutory"}]}]
    out = verify_reviews(reviews, _verify_law(tmp_path))
    f = out[0]["findings"][0]
    assert f["confidence"] == "suggestion"
    assert f["法律依据"] == []
    assert "未能核验" in f["问题描述"]


def test_verify_replaces_rewritten_text(tmp_path):
    reviews = [{"chapter": "A", "findings": [{
        "原文引用": "q", "风险类型": "权益", "问题描述": "x", "改进建议": "y",
        "法律依据": [{"law_name": "测试法", "article_no": "第一条",
                       "article_text": "违约金不超过损失30%。"}],
        "confidence": "statutory"}]}]
    out = verify_reviews(reviews, _verify_law(tmp_path))
    ref = out[0]["findings"][0]["法律依据"][0]
    assert ref["article_text"] == "违约金不得超过实际损失的百分之三十。"


# ---- Task 8 汇总节点:风险分级 + markdown 报告 + 结构化 JSON(设计 §4.5) ----

from agents.contract_review_agent.graph.report import build_report, risk_level, build_report_json  # noqa: E402


def test_risk_level_mapping():
    assert risk_level({"风险类型": "合规", "confidence": "statutory"}) == "高风险"
    assert risk_level({"风险类型": "漏洞", "confidence": "statutory"}) == "中风险"
    assert risk_level({"风险类型": "合规", "confidence": "suggestion"}) == "提示"


def test_build_report_includes_sections():
    reviews = [{"chapter": "第一章", "findings": [{
        "原文引用": "q", "风险类型": "合规", "问题描述": "问题", "改进建议": "建议",
        "法律依据": [{"law_name": "测试法", "article_no": "第一条",
                       "article_text": "违约金不得超过实际损失的百分之三十。"}],
        "confidence": "statutory"}]}]
    report = build_report(reviews, {"合同名称": "a.docx", "法条库版本": "v1",
                                    "审核时间": "2026-08-13"})
    assert "合同审核报告" in report
    assert "高风险" in report
    assert "测试法" in report


def test_build_report_json_stats():
    reviews = [{"chapter": "A", "findings": [
        {"原文引用": "q", "风险类型": "合规", "问题描述": "x", "改进建议": "y",
         "法律依据": [], "confidence": "suggestion"}]}]
    data = build_report_json(reviews)
    assert data["stats"]["高风险"] == 0
    assert data["stats"]["提示"] == 1


# ---- Task 9 F1 prompt 优化节点:mock LLM,不触真实 LLM(设计 §4.3) ----

from unittest.mock import MagicMock  # noqa: E402
from agents.contract_review_agent.graph.prompt_node import optimize_review_prompt  # noqa: E402


def test_optimize_review_prompt_returns_text():
    fake = MagicMock()
    fake.invoke.return_value.content = (
        "你是合同审核专家。\n一、审核范围…\n二、风险清单…\n三、输出格式…\n四、引用指引…")
    out = optimize_review_prompt("劳动合同", "重点看违约金", llm=fake)
    assert "审核" in out
    assert fake.invoke.call_args is not None


def test_optimize_review_prompt_keeps_ref_guidance():
    fake = MagicMock()
    fake.invoke.return_value.content = (
        "引用指引:法律依据只允许引用法条库片段原文,禁止编造;"
        "没有可依据的条款时标注'仅提示,非强制'。")
    out = optimize_review_prompt("劳动合同", "重点看违约金", llm=fake)
    assert "禁止编造" in out
    assert "引用指引" in out
