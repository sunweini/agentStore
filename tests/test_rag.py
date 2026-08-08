import json

import pytest

from agents.kingdee_plugin_agent.seed.seed_load import load_seed_data
from common.rag import RagClient, RagError


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
    assert n1 >= 5 and n2 == 0  # 二次灌入 0(幂等)


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
    assert "[已截断,剩余 2 个文件请检索]" in text
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
