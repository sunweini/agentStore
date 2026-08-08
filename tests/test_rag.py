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
