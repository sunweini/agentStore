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
