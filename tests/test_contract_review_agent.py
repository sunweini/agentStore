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


def test_retrieve_priority_labor_contract(tmp_path):
    """劳动合同域必查法条确定性注入(违约金限制条,不依赖检索召回)。

    用真实法条名(匹配 _PRIORITY 的"中华人民共和国劳动合同法")seed 一条
    第二十五条,断言 retrieve 恒带出它且标 priority。
    """
    from agents.contract_review_agent.store.law_store import LawStore

    s = LawStore(data_dir=tmp_path / "rag")
    s.seed("""# 中华人民共和国劳动合同法
来源: u
采集日期: 2026-08-13
领域: labor

## 第二十条
试用期工资不得低于本单位相同岗位最低档工资。

## 第二十五条
除本法第二十二条和第二十三条规定的情形外，用人单位不得与劳动者约定由劳动者承担违约金。""")
    hits = s.retrieve("违约金", "劳动合同", k=8)
    assert any(h["metadata"].get("priority") and
               h["metadata"].get("article_no") == "第二十五条"
               for h in hits)


def test_domain_substring_mapping():
    """合同类型全名("买卖合同")子串匹配到域(alias 键是短名"买卖")。"""
    from agents.contract_review_agent.store.law_store import _domain_of

    assert _domain_of("买卖合同") == "contract"
    assert _domain_of("劳动合同") == "labor"
    assert _domain_of("租赁合同") == "contract"
    assert _domain_of("借款") == "contract"
    assert _domain_of("未知类型") == ""


def test_retrieve_empty_query_returns_priority(tmp_path):
    """空正文章节(孤立标题)不做语义检索:嵌入服务拒绝空 input,只回必查法条。"""
    from agents.contract_review_agent.store.law_store import LawStore

    s = LawStore(data_dir=tmp_path / "rag")
    s.seed("""# 中华人民共和国劳动合同法
来源: u
采集日期: 2026-08-13
领域: labor

## 第二十五条
除本法第二十二条和第二十三条规定的情形外，用人单位不得与劳动者约定由劳动者承担违约金。""")
    hits = s.retrieve("", "劳动合同", k=8)
    assert any(h["metadata"].get("priority") for h in hits)


def test_review_all_skips_empty_chapter(monkeypatch):
    """空正文的孤立标题章节不进 LLM(无内容可审)。"""
    import agents.contract_review_agent.graph.nodes as nodes_mod
    from agents.contract_review_agent.graph.nodes import review_all

    called = []
    monkeypatch.setattr(nodes_mod, "review_chapter",
                        lambda llm, ct, ch, rp, law_store=None: called.append(ch["title"]))
    review_all({"chapters": [{"title": "孤立标题", "text": ""},
                             {"title": "有内容", "text": "正文"}],
                "contract_type": "劳动合同", "review_prompt": "审"},
               law_store=None)
    assert called == ["有内容"]


def test_load_bundled_from_md_dir(tmp_path):
    """LawStore(laws_dir=...) 构造即加载 md 精确索引,不 seed 也能 verify_ref(生产运行时关键)。"""
    laws_dir = tmp_path / "laws"
    laws_dir.mkdir()
    (laws_dir / "test_law.md").write_text(MD, encoding="utf-8")
    s = LawStore(data_dir=tmp_path / "rag", laws_dir=laws_dir)
    assert s.verify_ref("测试劳动合同法", "第一条") == "用人单位应当依法支付劳动报酬。"
    assert s.verify_ref("测试劳动合同法", "不存在的条") is None
    assert s.list_laws()[0]["count"] == 2
    assert s.list_laws()[0]["domain"] == "labor"


def test_load_bundled_builtin_laws(tmp_path):
    """内置 data/laws 三法:构造 LawStore 即核验通过(api.py/agent.py 生产 wiring 回归)。"""
    laws_dir = (Path(__file__).resolve().parent.parent
                / "agents/contract_review_agent/data/laws")
    s = LawStore(data_dir=tmp_path / "rag", laws_dir=laws_dir)
    names = {l["law_name"] for l in s.list_laws()}
    assert names == {"中华人民共和国劳动法", "中华人民共和国劳动合同法", "中华人民共和国民法典"}
    text = s.verify_ref("中华人民共和国劳动合同法", "第二十五条")
    assert text and "违约金" in text, "内置源精确核验应返回逐字原文"


def test_seed_batching_retry(tmp_path):
    """_add_batched:>BATCH 条分批灌 + 413 瞬态退避重试(审查 Important #2,嵌入服务单批上限)。"""
    from agents.contract_review_agent.store.law_store import _BATCH
    s = LawStore(data_dir=tmp_path / "rag")
    docs = [f"条款{i}" for i in range(41)]
    metas = [{"id": f"L:{i}"} for i in range(41)]
    batches: list[int] = []
    calls = {"n": 0}

    class Boom(RuntimeError):
        status_code = 413

    def fake(collection, docs_, metas_):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Boom()  # 首次调用模拟 413 → 应退避重试
        batches.append(len(docs_))

    s._client.add_documents = fake
    s._add_batched(docs, metas)
    assert batches == [_BATCH, _BATCH, 9], f"41 条应分 16+16+9,实测 {batches}"
    assert all(b <= _BATCH for b in batches)


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


def test_review_chapter_repairs_missing_ref_fields():
    """LLM 漏填 law_name/article_no(只回 article_text)→ 按片段匹配补齐,statutory 保留。"""
    fake = MagicMock()
    fake.invoke.return_value.content = (
        '{"chapter": "违约责任", "findings": [{"原文引用": "违约金50%", '
        '"风险类型": "合规", "问题描述": "可能过高", "改进建议": "调低", '
        '"法律依据": [{"article_text": "除本法第二十二条和第二十三条规定的情形外，'
        '用人单位不得与劳动者约定由劳动者承担违约金。"}], "confidence": "statutory"}]}'
    )
    class _FakeLawStore:
        def retrieve(self, query, contract_type, k=8):
            return [{"text": "除本法第二十二条和第二十三条规定的情形外,"
                              "用人单位不得与劳动者约定由劳动者承担违约金。",
                     "metadata": {"law_name": "中华人民共和国劳动合同法",
                                  "article_no": "第二十五条"}}]
    review = review_chapter(fake, contract_type="劳动合同", chapter={
        "title": "违约责任", "text": "违约金50%。"}, review_prompt="审",
        law_store=_FakeLawStore())
    assert review["findings"][0]["法律依据"][0]["law_name"] == "中华人民共和国劳动合同法"
    assert review["findings"][0]["法律依据"][0]["article_no"] == "第二十五条"


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


# ---- Task 11 独立计费/鉴权:SQLite 临时库,不碰生产 MySQL(设计 §5) ----

from common import db  # noqa: E402
from common.billing import (  # noqa: E402
    create_pending, commit, cancel_pending, check_quota, usage)
from common.apikey_mgmt import create_apikey  # noqa: E402


def test_billing_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "test.db"))
    db.init_tables()
    key = create_apikey("contract", "tester")["apikey"]
    create_pending(key, "contract", "task1")
    commit(key, "contract", "task1")
    u = usage(key, "contract")
    assert u["free"]["used"] == 1
    assert u["pending_count"] == 0


def test_commit_then_cancel_frees_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "test2.db"))
    db.init_tables()
    key = create_apikey("contract", "tester2")["apikey"]
    create_pending(key, "contract", "t2")
    cancel_pending(key, "contract", "t2")
    assert usage(key, "contract")["pending_count"] == 0


# ---- Task 11 安全加固(审查 Important):create_apikey role 校验 + 允许停用 admin ----

from fastapi import HTTPException  # noqa: E402
from common.apikey_mgmt import create_apikey, deactivate_apikey  # noqa: E402
from common.auth import check_apikey  # noqa: E402


def test_create_apikey_rejects_bad_role(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "test3.db"))
    db.init_tables()
    with pytest.raises(ValueError):
        create_apikey("contract", "hacker", role="superadmin")


def test_admin_can_deactivate_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "test4.db"))
    db.init_tables()
    admin1 = create_apikey("contract", "admin1", role="admin")["apikey"]
    admin2 = create_apikey("contract", "admin2", role="admin")["apikey"]
    # 管理员可软删另一个管理员(堵住"被铸 admin 永不可停用"的后门)
    deactivate_apikey("contract", admin2, admin1)
    with pytest.raises(HTTPException) as exc:
        check_apikey(admin2, "contract")
    assert exc.value.status_code == 401
    # 但不能停用自己
    with pytest.raises(HTTPException) as exc2:
        deactivate_apikey("contract", admin1, admin1)
    assert exc2.value.status_code == 403


# ---- Task 10 图构建:build_graph + run_review 全流水线(设计 §4.6) ----

from agents.contract_review_agent.graph.flows import build_graph  # noqa: E402
from agents.contract_review_agent.agent import run_review  # noqa: E402


def test_build_graph_smoke():
    graph = build_graph()
    assert graph is not None


def test_run_review_too_long():
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
        f.write(b"\x00" * 3000)
        p = f.name
    try:
        result = run_review(p, "劳动合同", "请审核", law_store=None)
        assert result["error"] in ("too_long", "unsupported")
    finally:
        Path(p).unlink(missing_ok=True)


# ---- Task 12 FastAPI 接口:TestClient 冒烟(health / 未鉴权 401/422) ----

from fastapi.testclient import TestClient  # noqa: E402
from agents.contract_review_agent.api import app  # noqa: E402


def test_health():
    c = TestClient(app)
    assert c.get("/health").status_code == 200


def test_review_requires_apikey():
    c = TestClient(app)
    files = {"file": ("x.docx", b"not a real docx", "application/octet-stream")}
    r = c.post("/api/v1/contract/review", files=files,
               data={"contract_type": "劳动合同", "prompt": "审"})
    assert r.status_code in (401, 422)


# ---- Task 12 审查修复:后台线程异常兜底 + 任务归属校验(审查 Critical/Important) ----


def test_review_background_exception_fallback(tmp_path, monkeypatch):
    """run_review 抛异常 → 任务转 failed(不 stuck running)+ cancel_pending 被调(审查 Critical #1)。"""
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "api.db"))
    db.init_tables()
    key = create_apikey("contract", "api_tester")["apikey"]

    import agents.contract_review_agent.api as api_module  # noqa: E402
    with patch("agents.contract_review_agent.agent.run_review",
               side_effect=RuntimeError("llm boom")), \
         patch.object(api_module.billing, "cancel_pending",
                      wraps=cancel_pending) as m:
        c = TestClient(app)
        files = {"file": ("x.docx", b"fake docx", "application/octet-stream")}
        r = c.post("/api/v1/contract/review", headers={"apikey": key},
                   files=files, data={"contract_type": "劳动合同", "prompt": "审"})
        assert r.status_code == 200
        assert m.called, "run_review 抛异常后应调用 cancel_pending"
        task_id = m.call_args[0][2]  # cancel_pending(apikey, agent, task_id) 第 3 参

    s = TestClient(app).get(f"/api/v1/contract/status?task_id={task_id}",
                            headers={"apikey": key})
    assert s.status_code == 200
    assert s.json()["status"] == "failed"
    assert s.json()["progress"] == 1.0
    # error 字段用通用码,不得含 str(exc)(审查 fix round 2:防凭据/敏感信息泄露)
    r2 = TestClient(app).get(f"/api/v1/contract/result?task_id={task_id}",
                             headers={"apikey": key})
    assert r2.status_code == 200
    assert r2.json()["result"]["error"] == "internal_error"
    assert "boom" not in str(r2.json()), "error 字段不得含异常详情(防 apikey/敏感信息泄露)"


def test_status_ownership_enforced(tmp_path, monkeypatch):
    """非本人 apikey 读他人任务 → 404(审查 Important #7,不泄露任务存在性)。"""
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "api2.db"))
    db.init_tables()
    owner = create_apikey("contract", "owner")["apikey"]
    other = create_apikey("contract", "other")["apikey"]

    import agents.contract_review_agent.api as api_module  # noqa: E402
    api_module._tasks["t_own"] = {"status": "running", "progress": 0.0,
                                  "result": None, "error": "",
                                  "apikey": owner, "request_id": "r1"}
    try:
        c = TestClient(app)
        # 他人 apikey → 404(与任务不存在同响应,不泄露)
        assert c.get("/api/v1/contract/status?task_id=t_own",
                     headers={"apikey": other}).status_code == 404
        # 主人 → 200
        assert c.get("/api/v1/contract/status?task_id=t_own",
                     headers={"apikey": owner}).status_code == 200
    finally:
        api_module._tasks.pop("t_own", None)


# ---- Task 15 OCR 接线:NeedsOcrError → 百度云端 OCR → 分章(全 mock,不真调百度) ----

import base64  # noqa: E402
import agents.contract_review_agent.graph.flows as flows  # noqa: E402
from agents.contract_review_agent.utils import ocr_client  # noqa: E402
from agents.contract_review_agent.utils.document_parser import NeedsOcrError  # noqa: E402


def _force_needs_ocr(path):
    raise NeedsOcrError("PDF 无文本层,需要 OCR")


def _ocr_state():
    return {"_file_path": "/tmp/scan.pdf", "_file_name": "scan.pdf"}


def _patch_needs_ocr(monkeypatch):
    """让 parse_document 恒抛 NeedsOcrError,触发 _parse_node 的 OCR 分支。"""
    monkeypatch.setattr(
        "agents.contract_review_agent.utils.document_parser.parse_document",
        _force_needs_ocr)


def test_get_token_missing_creds_returns_empty(monkeypatch):
    """缺 BAIDU_OCR_* 任一 → get_token 返回空串,不发起网络请求。"""
    monkeypatch.delenv("BAIDU_OCR_API_KEY", raising=False)
    monkeypatch.delenv("BAIDU_OCR_SECRET_KEY", raising=False)
    assert ocr_client.get_token() == ""


def test_get_token_with_creds(monkeypatch):
    """配了凭据 → get_token 走 get_baidu_token 换 access_token。"""
    monkeypatch.setenv("BAIDU_OCR_API_KEY", "ak")
    monkeypatch.setenv("BAIDU_OCR_SECRET_KEY", "sk")
    with patch("httpx.post") as m:
        m.return_value.status_code = 200
        m.return_value.json.return_value = {"access_token": "tok-abc"}
        assert ocr_client.get_token() == "tok-abc"


def test_ocr_image_bytes_sends_base64_str():
    """base64 编码必须转成 str 再传(Baidu API 收 bytes 会 400;修复回归)。"""
    with patch("httpx.post") as m:
        m.return_value.status_code = 200
        m.return_value.json.return_value = {"words_result": []}
        ocr_client.ocr_image_bytes(b"\x89PNG", "tok")
        sent = m.call_args.kwargs["data"]["image"]
        assert isinstance(sent, str), "image 字段必须是 base64 字符串,不能是 bytes"
        assert base64.b64decode(sent) == b"\x89PNG"


def test_parse_node_ocr_with_token(monkeypatch):
    """NeedsOcrError → 有 token → OCR 文本 → 启发式分章产出 chapters(不返 error)。"""
    _patch_needs_ocr(monkeypatch)
    monkeypatch.setattr(ocr_client, "get_token", lambda: "tok-abc")
    monkeypatch.setattr(ocr_client, "ocr_pdf_pages",
                        lambda path, token: "第一条 总则\n本合同受中华人民共和国法律管辖,双方因履行本合同发生争议时应友好协商解决。\n第二条 价款")
    out = flows._parse_node(_ocr_state(), {})
    assert out.get("error", "") == ""
    assert out["chapters"], "OCR 文本应产出章节"
    assert out["chapters"][0]["title"] == "第一条 总则"
    assert "法律管辖" in out["chapters"][0]["text"]
    assert out["chapters"][1]["title"] == "第二条 价款"


def test_parse_node_ocr_no_token(monkeypatch):
    """无 token(缺凭据)→ ocr_unconfigured,且不调 OCR。"""
    _patch_needs_ocr(monkeypatch)
    monkeypatch.setattr(ocr_client, "get_token", lambda: "")
    called = {"n": 0}

    def _should_not_call(path, token):
        called["n"] += 1
        raise AssertionError("无 token 不应调用 ocr_pdf_pages")

    monkeypatch.setattr(ocr_client, "ocr_pdf_pages", _should_not_call)
    out = flows._parse_node(_ocr_state(), {})
    assert out == {"error": "ocr_unconfigured"}
    assert called["n"] == 0


def test_parse_node_ocr_failed_on_exception(monkeypatch):
    """OCR 抛异常 → ocr_failed(结构化日志只记 error_type,不泄露 str(exc))。"""
    _patch_needs_ocr(monkeypatch)
    monkeypatch.setattr(ocr_client, "get_token", lambda: "tok-abc")

    def _boom(path, token):
        raise RuntimeError("baidu 500 secret-payload")

    monkeypatch.setattr(ocr_client, "ocr_pdf_pages", _boom)
    out = flows._parse_node(_ocr_state(), {})
    assert out == {"error": "ocr_failed"}


def test_parse_node_ocr_failed_on_empty(monkeypatch):
    """OCR 返回空文本 → ocr_failed(不产出空章节)。"""
    _patch_needs_ocr(monkeypatch)
    monkeypatch.setattr(ocr_client, "get_token", lambda: "tok-abc")
    monkeypatch.setattr(ocr_client, "ocr_pdf_pages", lambda path, token: "   \n  ")
    out = flows._parse_node(_ocr_state(), {})
    assert out == {"error": "ocr_failed"}
