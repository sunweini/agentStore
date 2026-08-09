import json

import pytest

from agents.kingdee_plugin_agent.seed.seed_load import load_seed_data
from common.rag import RagClient, RagError, _embedding_model


# ---- embedding 模型配置化(EMBEDDING_* env 分支;autouse 夹具已清除 env + 清缓存) ----


def test_embedding_model_huggingface_default(monkeypatch):
    """无 EMBEDDING_* env:走 huggingface 默认(BAAI/bge-small-zh-v1.5,离线本地)。"""
    from langchain_huggingface.embeddings import HuggingFaceEmbeddings

    model = _embedding_model()
    assert isinstance(model, HuggingFaceEmbeddings)
    assert model.model_name == "BAAI/bge-small-zh-v1.5"


def test_embedding_model_huggingface_custom_model(monkeypatch):
    """EMBEDDING_PROVIDER=huggingface + 自定义 EMBEDDING_MODEL:env 透传构造参数。

    mock 构造函数断言(真实构造会用不存在的 hub 模型名发起下载)。
    """
    import common.rag as rag_module

    captured = {}

    class _FakeHF:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(rag_module, "HuggingFaceEmbeddings", _FakeHF)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "huggingface")
    monkeypatch.setenv("EMBEDDING_MODEL", "custom/zh-embed")
    _embedding_model()
    assert captured["model_name"] == "custom/zh-embed"
    assert captured["encode_kwargs"] == {"normalize_embeddings": True}


def test_embedding_model_openai_compatible_defaults(monkeypatch):
    """openai-compatible 分支:OpenAIEmbeddings + 默认 Qwen 模型 + 指定 base_url。"""
    from langchain_openai import OpenAIEmbeddings

    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai-compatible")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://10.33.17.234:32320")
    model = _embedding_model()
    assert isinstance(model, OpenAIEmbeddings)
    assert model.model == "Qwen/Qwen3-Embedding-8B"  # openai-compatible 缺省模型
    assert model.openai_api_base == "http://10.33.17.234:32320"


def test_embedding_model_openai_compatible_custom(monkeypatch):
    """openai-compatible 分支:自定义模型 + API key 透传。"""
    from langchain_openai import OpenAIEmbeddings

    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai-compatible")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://embed.example/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "custom/embed-8B")
    monkeypatch.setenv("EMBEDDING_API_KEY", "sk-embed-key")
    model = _embedding_model()
    assert isinstance(model, OpenAIEmbeddings)
    assert model.model == "custom/embed-8B"
    assert model.openai_api_base == "http://embed.example/v1"
    assert model.openai_api_key.get_secret_value() == "sk-embed-key"  # SecretStr 需解包


def test_embedding_model_openai_compatible_missing_base_url(monkeypatch):
    """openai-compatible 缺 EMBEDDING_BASE_URL:抛清晰错误(不静默回退)。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai-compatible")
    with pytest.raises(RagError, match="EMBEDDING_BASE_URL"):
        _embedding_model()


def test_embedding_model_unknown_provider_raises(monkeypatch):
    """未知 EMBEDDING_PROVIDER(拼写错误如 "openaicompatible"):抛 RagError,
    不静默回退 huggingface(静默回退会把误配置当成本地模型,检索静默失真)。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openaicompatible")
    with pytest.raises(RagError, match="EMBEDDING_PROVIDER"):
        _embedding_model()


def test_embedding_model_empty_model_uses_default_hf(monkeypatch):
    """EMBEDDING_MODEL= 空串(非未配置):huggingface 分支回落默认模型。"""
    from langchain_huggingface.embeddings import HuggingFaceEmbeddings

    monkeypatch.setenv("EMBEDDING_PROVIDER", "huggingface")
    monkeypatch.setenv("EMBEDDING_MODEL", "")
    model = _embedding_model()
    assert isinstance(model, HuggingFaceEmbeddings)
    assert model.model_name == "BAAI/bge-small-zh-v1.5"


def test_embedding_model_empty_model_uses_default_openai(monkeypatch):
    """EMBEDDING_MODEL= 空串(非未配置):openai-compatible 分支回落默认模型。"""
    from langchain_openai import OpenAIEmbeddings

    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai-compatible")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://10.33.17.234:32320")
    monkeypatch.setenv("EMBEDDING_MODEL", "")
    model = _embedding_model()
    assert isinstance(model, OpenAIEmbeddings)
    assert model.model == "Qwen/Qwen3-Embedding-8B"


def test_rag_client_creates_dirs(tmp_path):
    client = RagClient(data_dir=tmp_path)
    assert (tmp_path / "chroma").exists()


def test_add_and_search_roundtrip(tmp_path):
    client = RagClient(data_dir=tmp_path)
    client.add_documents("api_ref", ["Kingdee.BOS.Core.Metadata 是元数据命名空间"], [{"ns": "Kingdee.BOS"}])
    hits = client.search("api_ref", "元数据", k=1)
    assert hits[0]["metadata"]["ns"] == "Kingdee.BOS"
    assert "元数据" in hits[0]["text"]
    assert "score" in hits[0]


def test_one_client_multi_collection(tmp_path):
    """同一 RagClient、同一 chroma 路径下多 collection:各库独立可写可查。"""
    client = RagClient(data_dir=tmp_path)
    client.add_documents("api_ref", ["Kingdee.BOS.Core.Metadata 是元数据命名空间"], [{"ns": "Kingdee.BOS"}])
    client.add_documents("guide", ["向导:创建插件工程时选择 BOS 插件类型"], [{"topic": "plugin"}])
    hits_api = client.search("api_ref", "元数据", k=1)
    hits_guide = client.search("guide", "插件工程", k=1)
    assert hits_api[0]["metadata"]["ns"] == "Kingdee.BOS"
    assert hits_guide[0]["metadata"]["topic"] == "plugin"


def test_two_clients_shared_dir(tmp_path):
    """两个 RagClient 共享同一 data_dir:不能各自开同路径 persistent client 互相打架。"""
    client1 = RagClient(data_dir=tmp_path)
    client2 = RagClient(data_dir=tmp_path)
    client1.add_documents("api_ref", ["Kingdee.BOS.Core.Metadata 是元数据命名空间"], [{"ns": "Kingdee.BOS"}])
    hits = client2.search("api_ref", "元数据", k=1)
    assert hits[0]["metadata"]["ns"] == "Kingdee.BOS"


def test_search_empty_collection(tmp_path):
    client = RagClient(data_dir=tmp_path)
    assert client.search("api_ref", "任何", k=1) == []


def test_unknown_collection_raises(tmp_path):
    client = RagClient(data_dir=tmp_path)
    with pytest.raises(RagError):
        client.add_documents("not_a_library", ["x"], [{}])


def test_seed_load_idempotent(tmp_path):
    client = RagClient(data_dir=tmp_path)
    n1 = load_seed_data(client)
    n2 = load_seed_data(client)
    assert n1 >= 10 and n2 == 0  # 13 条(含签名类 CS0506/CS0115、真实环境 MSB3274/3275、CS0246-EventArgs、Roslyn 相关 CS1056/MSB4067/TimeoutExpired 种子);二次灌入 0(幂等)
    # 种子文本与 ExperienceStore.propose 格式统一(设计 §6.2:种子即 w7 格式样本)
    hits = client.search("experience", "CS0246", k=5, filter={"signature": "CS0246|"})
    assert hits[0]["text"] == "[CS0246] 命名空间或类型找不到(缺 Kingdee.BOS 引用) 修复:csproj 加 Reference 到 Kingdee.BOS.dll"


from common.rag import StandardsLoader


def test_standards_inject_within_budget(tmp_path):
    (tmp_path / "rule1.md").write_text("规则一:事件签名必须匹配元数据\n", encoding="utf-8")
    (tmp_path / "rule2.md").write_text("规则二:异常必须记录日志\n", encoding="utf-8")
    loader = StandardsLoader(standards_dir=tmp_path)
    text = loader.inject_text(limit_tokens=100000)  # 大预算:全量注入
    assert "规则一" in text and "规则二" in text


def test_standards_truncate_over_budget(tmp_path):
    big = "规则:" + "内容" * 5000
    (tmp_path / "big.md").write_text(big, encoding="utf-8")
    (tmp_path / "small.md").write_text("小规则\n", encoding="utf-8")
    loader = StandardsLoader(standards_dir=tmp_path)
    text = loader.inject_text(limit_tokens=100)  # 小预算:截断 + 标注
    assert "[已截断,剩余 2 个文件,请调用 guide_fallback 检索]" in text
    assert "内容" not in text  # 大文件正文不得残留


def test_standards_empty_dir(tmp_path):
    loader = StandardsLoader(standards_dir=tmp_path)
    assert loader.inject_text() == ""


def test_hybrid_search_returns_merged(tmp_path):
    client = RagClient(data_dir=tmp_path)
    client.add_documents("api_ref",
        ["Kingdee.BOS.Core.Metadata.FormMetadata 表单元数据",
         "BOS 平台扩展字段用法"], [{"ns": "Kingdee.BOS"}]*2)
    hits = client.hybrid_search("api_ref", "FormMetadata", k=2, bm25_weight=0.7)
    assert len(hits) >= 1
    assert all("text" in h for h in hits)


def test_hybrid_search_empty(tmp_path):
    client = RagClient(data_dir=tmp_path)
    assert client.hybrid_search("guide", "任何", k=2) == []


def test_hybrid_search_metadata_filter(tmp_path):
    """filter 契约:简单 {key: value} 相等过滤,BM25/向量两通道均只返回匹配文档。"""
    client = RagClient(data_dir=tmp_path)
    client.add_documents("api_ref",
        ["Kingdee.BOS 单据提交操作说明", "Kingdee.BOS 服务端插件注册向导"],
        [{"plugin_type": "bill"}, {"plugin_type": "service"}])
    for w in (1.0, 0.0):  # 纯 BM25 与纯向量通道都要过滤
        hits = client.hybrid_search("api_ref", "Kingdee.BOS", k=5, bm25_weight=w,
                                    filter={"plugin_type": "bill"})
        assert hits, f"bm25_weight={w}: 应有命中"
        assert all(h["metadata"].get("plugin_type") == "bill" for h in hits)
        assert all("单据" in h["text"] for h in hits)


def test_hybrid_search_weighting_logic(tmp_path, monkeypatch):
    """混合检索融合逻辑钉桩:固定向量排名(B 优先),验证权重翻转首位命中。

    真实 bge-small-zh 嵌入对单 token 查询偏词面(A 含精确 token 时向量通道
    也偏好 A),无法用真实语料让两通道天然分歧;故钉桩向量通道为固定排名,
    专注验证加权 RRF 融合数学(BM25 通道仍走真实实现)。
    """
    from langchain_core.documents import Document

    client = RagClient(data_dir=tmp_path)
    doc_a = "FormMetadata 是缓存键,与元数据无关"  # 含精确 API token,语义无关
    doc_b = "BOS 平台表单元数据 的字段结构定义"  # 语义相关,无精确 token
    client.add_documents("api_ref", [doc_a, doc_b], [{"ns": "Kingdee.BOS"}]*2)
    store = client._store("api_ref")
    all_data = store.get()
    id_a = next(i for i, t in zip(all_data["ids"], all_data["documents"]) if t == doc_a)
    id_b = next(i for i, t in zip(all_data["ids"], all_data["documents"]) if t == doc_b)

    # 钉桩向量通道:固定返回 B 第一、A 第二(模拟向量通道偏好语义命中)
    def fake_vec(query, k=5):
        return [
            (Document(page_content=doc_b, metadata={"ns": "Kingdee.BOS"}, id=id_b), 0.3),
            (Document(page_content=doc_a, metadata={"ns": "Kingdee.BOS"}, id=id_a), 0.5),
        ]

    monkeypatch.setattr(store, "similarity_search_with_score", fake_vec)

    # (a) 纯 BM25:精确 token 命中 A 置顶
    hits = client.hybrid_search("api_ref", "FormMetadata", k=2, bm25_weight=1.0)
    assert hits[0]["text"] == doc_a
    # (b) 纯向量:语义命中 B 置顶(来自钉桩固定排名)
    hits = client.hybrid_search("api_ref", "FormMetadata", k=2, bm25_weight=0.0)
    assert hits[0]["text"] == doc_b
    # (c) 加权 0.7:RRF 融合后精确命中 A 仍置顶(A=0.7/61+0.3/62 > B=0.3/61)
    hits = client.hybrid_search("api_ref", "FormMetadata", k=2, bm25_weight=0.7)
    assert hits[0]["text"] == doc_a
    # 结果结构:每条含 text/score/metadata 键
    assert all(set(h) == {"text", "score", "metadata"} for h in hits)


def test_hybrid_search_bm25_weight_out_of_range_raises(tmp_path):
    client = RagClient(data_dir=tmp_path)
    with pytest.raises(ValueError):
        client.hybrid_search("api_ref", "任何", bm25_weight=1.5)


from common.rag import ExperienceStore


def test_experience_dedup_by_signature(tmp_path):
    client = RagClient(data_dir=tmp_path)
    store = ExperienceStore(client)
    sig1 = store.propose("CS0103", "", "名称不存在", "核对字段名")
    sig2 = store.propose("CS0103", "", "名称不存在", "核对字段名")
    assert sig1 == sig2  # 同签名去重
    # 库内仅一条(二次 propose 不重复插入)
    assert len(client.search("experience", "CS0103", k=10)) == 1


def test_experience_verify_flow(tmp_path):
    client = RagClient(data_dir=tmp_path)
    store = ExperienceStore(client)
    sig = store.propose("CS0246", "Plugin.cs", "类型找不到", "加引用")
    assert store.search_related("CS0246", "类型找不到")[0]["metadata"]["status"] == "proposed"
    store.verify(sig)
    assert store.search_related("CS0246", "类型找不到")[0]["metadata"]["status"] == "verified"


def test_experience_verify_unknown_signature_raises(tmp_path):
    client = RagClient(data_dir=tmp_path)
    store = ExperienceStore(client)
    with pytest.raises(RagError):
        store.verify("CS9999|None.cs")


def test_experience_confidence_marking(tmp_path):
    """接口契约:proposed 标记 confidence=unverified;verified 标记 confidence=verified。"""
    client = RagClient(data_dir=tmp_path)
    store = ExperienceStore(client)
    sig = store.propose("CS0019", "Form1.cs", "运算符不适用于操作数", "加类型转换")
    assert store.search_related("CS0019", "运算符")[0]["metadata"]["confidence"] == "unverified"
    store.verify(sig)
    hit = store.search_related("CS0019", "运算符")[0]
    assert hit["metadata"]["status"] == "verified"
    assert hit["metadata"]["confidence"] == "verified"


def test_experience_archive_flow(tmp_path):
    """接口契约:archive() 置 status=archived 并过滤出检索(proposed 与 verified 均可归档)。"""
    client = RagClient(data_dir=tmp_path)
    store = ExperienceStore(client)
    # proposed 归档
    sig = store.propose("CS0201", "Plugin.cs", "常量无效", "检查常量定义")
    assert store.search_related("CS0201", "常量")[0]["metadata"]["status"] == "proposed"
    store.archive(sig)
    assert store.search_related("CS0201", "常量") == []      # archived 被过滤
    # verified 归档
    sig2 = store.propose("CS0161", "", "并非所有代码路径都返回值", "补 return")
    store.verify(sig2)
    store.archive(sig2)
    assert store.search_related("CS0161", "返回值") == []
    # 文档与元数据仍在库内(status 翻转只改元数据,防重复 propose 误判幂等)
    hits = client.search("experience", "CS0201", k=10)
    assert hits and hits[0]["metadata"]["status"] == "archived"


def test_experience_archive_unknown_signature_raises(tmp_path):
    client = RagClient(data_dir=tmp_path)
    store = ExperienceStore(client)
    with pytest.raises(RagError):
        store.archive("CS9999|None.cs")
