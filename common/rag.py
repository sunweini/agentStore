"""RAG 四库统一客户端。

库划分:
  api_ref / guide / experience ──► Chroma 向量库(混合检索)
  standards ──► 纯 markdown 整库注入(见 Task B3)

API 依据(铁律:以官方文档为准,不凭记忆):
  - langchain-chroma Chroma 构造参数:collection_name / embedding_function /
    persist_directory(官方 API 参考 langchain_chroma.vectorstores.Chroma 确认,
    未改名 path)。
  - similarity_search_with_score(query, k=, filter=):filter 为 chromadb 元数据
    过滤条件(官方 vectorstore 集成文档示例 filter={"source": "news"})。
  - 嵌入用 langchain_huggingface.HuggingFaceEmbeddings(sentence-transformers
    后端):HuggingFaceBgeEmbeddings 已在 langchain 0.2.2 弃用、1.0 移除,
    langchain-huggingface 1.x 不再提供,官方迁移路径即 HuggingFaceEmbeddings。
"""
from functools import lru_cache
from pathlib import Path

from langchain_chroma import Chroma
from langchain_huggingface.embeddings import HuggingFaceEmbeddings

RAG_COLLECTIONS = ("api_ref", "guide", "experience")


class RagError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _embedding_model():
    """BGE 中文嵌入,全局单例(模型加载一次,~2GB 内存)。"""
    return HuggingFaceEmbeddings(
        model_name="BAAI/bge-small-zh-v1.5",
        encode_kwargs={"normalize_embeddings": True},
    )


class RagClient:
    def __init__(self, data_dir: Path = Path("data/kingdee-rag")):
        self.data_dir = data_dir
        self.chroma_dir = data_dir / "chroma"
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        self._stores: dict[str, Chroma] = {}

    def _store(self, collection: str) -> Chroma:
        if collection not in RAG_COLLECTIONS:
            raise RagError(f"未知库: {collection}")
        if collection not in self._stores:
            self._stores[collection] = Chroma(
                collection_name=collection,
                embedding_function=_embedding_model(),
                persist_directory=str(self.chroma_dir),
            )
        return self._stores[collection]

    def add_documents(self, collection: str, docs: list[str], metadatas: list[dict]) -> None:
        store = self._store(collection)
        store.add_texts(docs, metadatas=metadatas)

    def search(self, collection: str, query: str, k: int = 5, filter: dict | None = None) -> list[dict]:
        store = self._store(collection)
        hits = store.similarity_search_with_score(query, k=k, filter=filter)
        return [{"text": h.page_content, "score": s, "metadata": h.metadata} for h, s in hits]
