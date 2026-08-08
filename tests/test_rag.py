import pytest

from common.rag import RagClient, RagError


def test_rag_client_creates_dirs(tmp_path):
    client = RagClient(data_dir=tmp_path)
    assert (tmp_path / "chroma").exists()


def test_add_and_search_roundtrip(tmp_path):
    client = RagClient(data_dir=tmp_path)
    client.add_documents("api_ref", ["Kingdee.BOS.Core.Metadata 是元数据命名空间"], [{"ns": "Kingdee.BOS"}])
    hits = client.search("api_ref", "元数据", k=1)
    assert hits[0]["metadata"]["ns"] == "Kingdee.BOS"


def test_search_empty_collection(tmp_path):
    client = RagClient(data_dir=tmp_path)
    assert client.search("api_ref", "任何", k=1) == []


def test_unknown_collection_raises(tmp_path):
    client = RagClient(data_dir=tmp_path)
    with pytest.raises(RagError):
        client.add_documents("not_a_library", ["x"], [{}])
