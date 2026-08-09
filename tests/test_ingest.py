"""RAG 导入管线测试(agents/kingdee_plugin_agent/tools/ingest.py)。

覆盖(任务要求):
  - code_aware_chunk:代码围栏跨段落整体保留、围栏内部绝不切分(即使超长);
  - ingest_dir:tmp 目录 2 个 md → 入库可检索(含 frontmatter 剔除、幂等);
  - ingest_url:mock HTTP(带 script/nav 噪音的假 HTML)→ 干净文本入库、噪音剔除;
  - CLI --dir 可运行。

真实数据(金蝶官方爬取)不进测试 —— 联网/数据不稳定的用例用 mock 或 tmp 目录,
避免测试依赖爬取结果。
"""

import pytest

from common.rag import RagClient
from agents.kingdee_plugin_agent.tools import ingest as mod
from agents.kingdee_plugin_agent.tools.ingest import (
    IngestError,
    code_aware_chunk,
    clean_text,
    html_to_text,
    ingest_dir,
    ingest_url,
    main,
    normalize_title,
    strip_frontmatter,
)


# ---------------------------------------------------------------------------
# code_aware_chunk
# ---------------------------------------------------------------------------

def test_chunk_splits_on_paragraph_boundaries():
    text = "段落一。\n\n段落二。\n\n段落三。"
    chunks = code_aware_chunk(text, max_chars=10)  # 每段都超限 → 逐段成块
    assert chunks == ["段落一。", "段落二。", "段落三。"]


def test_chunk_keeps_code_fence_intact_across_paragraphs():
    """围栏跨越多个段落(围栏内含空行) → 整体单一 chunk,绝不在围栏内切分。"""
    text = (
        "前言段落。\n\n"
        "```csharp\n"
        "public void A()\n"
        "{\n"
        "    var x = 1;\n"
        "}\n"
        "\n"
        "// 围栏内空行分隔的两段代码\n"
        "Console.WriteLine(x);\n"
        "```\n\n"
        "后记段落。"
    )
    chunks = code_aware_chunk(text, max_chars=80)
    fences = [c for c in chunks if "```" in c]
    assert len(fences) == 1  # 围栏只出现一次 → 未被切开
    assert fences[0].count("```") == 2  # 开/闭标记齐全
    assert "// 围栏内空行分隔" in fences[0]
    assert chunks[0] == "前言段落。" and chunks[-1] == "后记段落。"  # 前后文本各自成块


def test_chunk_oversized_fence_stays_single_chunk():
    """围栏远超 max_chars → 仍整体保留(任务硬性要求)。"""
    fence = "```python\n" + ("x = 1\n" * 500) + "```"
    chunks = code_aware_chunk("前\n\n" + fence + "\n\n后", max_chars=300)
    assert len(chunks) == 3
    assert chunks[1] == fence.strip()
    assert all(len(c) < len(fence) for c in chunks if c != chunks[1])


def test_chunk_unterminated_fence_kept():
    chunks = code_aware_chunk("a\n\n```\nopen code\n```\nb\n\n```\nnever closed\n", max_chars=50)
    assert any("never closed" in c for c in chunks)


def test_chunk_splits_long_paragraph_at_sentence_boundary():
    text = "第一句。" * 200 + "结尾句。"  # 300+ 字、单段落
    chunks = code_aware_chunk(text, max_chars=300)
    assert len(chunks) >= 2
    assert all(len(c) <= 310 for c in chunks)
    assert "".join(chunks) == text  # 无内容丢失


# ---------------------------------------------------------------------------
# HTML 清洗 / frontmatter / 标题
# ---------------------------------------------------------------------------

_FAKE_HTML = """<html><head><title>收款单插件扩展实操 - 金蝶开发者社区</title></head>
<body><nav>首页 知识库 问答</nav>
<script>var noise = 1;</script>
<h1>收款单插件扩展实操</h1>
<div class="content"><p>本文演示如何扩展收款单插件。</p>
<p>通过 AfterDoOperationEventArgs 事件实现。</p>
<p>事件参数携带操作信息,插件可在操作前后拦截。</p>
<p>硬拦截时通过 e.Cancel 阻止操作继续执行。</p>
<pre><code>public class MyPlugIn : AbstractOperationServicePlugIn
{
}</code></pre></div>
<footer>分享 收藏 评论</footer></body></html>"""


def test_html_to_text_strips_noise_keeps_body():
    text = clean_text(html_to_text(_FAKE_HTML))
    assert "noise" not in text and "首页" not in text  # script/nav 剔除
    assert "分享" not in text and "收藏" not in text  # 样板行剔除
    assert "AfterDoOperationEventArgs" in text
    assert "public class MyPlugIn" in text  # pre 代码保留


def test_normalize_title_priority_and_fallback():
    assert normalize_title("http://x/a/1", _FAKE_HTML) == "收款单插件扩展实操"
    # 无 <title> → 首个 <h1>
    html = "<html><body><h1>BOS 知识地图</h1><p>正文</p></body></html>"
    assert normalize_title("http://x/a/2", html) == "BOS 知识地图"
    # 两者皆无 → URL 尾段
    assert normalize_title("http://x/a/3", "<html><body>正文</body></html>") == "3"


def test_strip_frontmatter():
    md = "---\ntitle: B\ntags: [x]\n---\n# B 配置\n内容"
    assert strip_frontmatter(md).startswith("# B 配置")
    assert strip_frontmatter("# 直接标题").startswith("# 直接标题")


# ---------------------------------------------------------------------------
# ingest_dir / ingest_url / CLI(轻量 mock,RagClient 用 tmp 数据目录)
# ---------------------------------------------------------------------------

def _store_docs(data_dir, collection):
    client = RagClient(data_dir=data_dir)
    return client._store(collection).get()


def test_ingest_dir_stores_and_searchable(tmp_path):
    src = tmp_path / "docs"
    src.mkdir()
    (src / "a.md").write_text(
        "# 插件工程向导\n\n创建插件工程时选择 BOS 插件类型,并勾选单据插件模板。\n",
        encoding="utf-8",
    )
    (src / "b.md").write_text(
        "---\ntitle: 隐藏元数据\n---\n# 数据读取\n\n使用 BusinessDataServiceHelper 读取单据数据。\n",
        encoding="utf-8",
    )
    data_dir = tmp_path / "rag"
    n = ingest_dir(src, "guide", data_dir=data_dir)
    assert n >= 2

    found = _store_docs(data_dir, "guide")
    docs, metas = found["documents"], found["metadatas"]
    sources = {m["source"] for m in metas}
    assert sources == {"a.md", "b.md"}
    all_text = "\n".join(docs)
    assert "插件工程向导" in all_text
    assert "BusinessDataServiceHelper" in all_text
    assert "隐藏元数据" not in all_text  # frontmatter 已剔除

    hits = RagClient(data_dir=data_dir).hybrid_search("guide", "插件工程", k=3)
    assert hits and "插件工程" in hits[0]["text"]  # 入库即可检索

    # 幂等:重跑新增 0
    assert ingest_dir(src, "guide", data_dir=data_dir) == 0


def test_ingest_dir_all_failed_raises(tmp_path, monkeypatch):
    src = tmp_path / "docs"
    src.mkdir()
    (src / "x.md").write_text("内容\n", encoding="utf-8")

    def boom(*a, **k):
        raise IngestError("存储不可用")

    monkeypatch.setattr(mod, "_ingest_md_file", boom)
    with pytest.raises(IngestError, match="全部"):
        ingest_dir(src, "guide", data_dir=tmp_path / "rag")


def test_ingest_url_clean_store_and_noise_absent(tmp_path, monkeypatch):
    url = "https://example.com/article/123"
    monkeypatch.setattr(mod, "fetch_html", lambda u: _FAKE_HTML)
    data_dir = tmp_path / "rag"
    n = ingest_url(url, "guide", data_dir=data_dir)
    assert n >= 1

    found = _store_docs(data_dir, "guide")
    docs, metas = found["documents"], found["metadatas"]
    all_text = "\n".join(docs)
    assert "收款单插件扩展实操" in all_text
    assert "noise" not in all_text and "首页" not in all_text
    assert "分享" not in all_text and "收藏" not in all_text
    assert "AfterDoOperationEventArgs" in all_text
    assert all(m["source"] == url for m in metas)
    assert all("收款单插件扩展实操" in m["title"] for m in metas)

    # 幂等:同 URL 重跑新增 0
    assert ingest_url(url, "guide", data_dir=data_dir) == 0


def test_ingest_url_http_error_raises_clear_message(tmp_path, monkeypatch):
    def fail(url):
        raise IngestError("HTTP 404: https://example.com/missing")

    monkeypatch.setattr(mod, "fetch_html", fail)
    with pytest.raises(IngestError, match="HTTP 404"):
        ingest_url("https://example.com/missing", "guide", data_dir=tmp_path / "rag")


def test_ingest_url_unknown_collection(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "fetch_html", lambda u: _FAKE_HTML)
    with pytest.raises(IngestError, match="未知库"):
        ingest_url("https://example.com/a", "not_a_lib", data_dir=tmp_path / "rag")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_dir_runs_and_reports(tmp_path, capsys):
    src = tmp_path / "docs"
    src.mkdir()
    (src / "x.md").write_text("# X 主题\n\n可检索内容。\n", encoding="utf-8")
    data_dir = tmp_path / "rag"
    rc = main(["--dir", str(src), "--collection", "guide", "--data-dir", str(data_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "docs" in out and "guide" in out and "+1 chunks" in out
    hits = RagClient(data_dir=data_dir).search("guide", "可检索内容", k=1)
    assert hits and "可检索内容" in hits[0]["text"]


def test_cli_url_failure_exit_nonzero(tmp_path, capsys, monkeypatch):
    def fail(url):
        raise IngestError("HTTP 500: https://example.com/broken")

    monkeypatch.setattr(mod, "fetch_html", fail)
    rc = main(["--url", "https://example.com/broken", "--collection", "api_ref",
               "--data-dir", str(tmp_path / "rag")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "HTTP 500" in err and "全部 URL 导入失败" in err


def test_cli_multi_url_partial_failure_continues(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(mod, "fetch_html", lambda u: _FAKE_HTML if "ok" in u else (_ for _ in ()).throw(IngestError("HTTP 404: fail")))
    rc = main(["--url", "https://example.com/fail", "--url", "https://example.com/ok",
               "--collection", "guide", "--data-dir", str(tmp_path / "rag")])
    assert rc == 0  # 部分成功 → 0,失败已 log
    out = capsys.readouterr().out
    assert "https://example.com/ok" in out
    assert "+" in out


def test_cli_no_args_prints_help_and_exit2(capsys):
    with pytest.raises(SystemExit):
        main([])
