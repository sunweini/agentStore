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
  - Chroma.get():公开 API,返回 {"ids","documents","metadatas",...},空库返回
    空列表;query 结果 Document 携带 chroma id(实测 langchain-chroma 1.1.0)。
  - 嵌入用 langchain_huggingface.HuggingFaceEmbeddings(sentence-transformers
    后端):HuggingFaceBgeEmbeddings 已在 langchain 0.2.2 弃用、1.0 移除,
    langchain-huggingface 1.x 不再提供,官方迁移路径即 HuggingFaceEmbeddings。
  - 混合检索:官方方案为 EnsembleRetriever(BM25Retriever + 向量 retriever),
    融合公式为加权倒数排名(weighted RRF):score(doc) = Σ_i w_i / (rank_i + c),
    rank 从 1 起、c 默认 60(官方源码 langchain/retrievers/ensemble.py 确认)。
    本环境 langchain 1.3.14 已移除 langchain.retrievers,且 langchain_community
    未安装(requirements.txt 无此依赖),故按上述官方语义内建等效实现:
    纯 Python Okapi BM25(k1=1.5, b=0.75)+ 加权 RRF 融合,不引入新依赖。
"""
import re
from collections import defaultdict
from functools import lru_cache
from math import log
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

    def hybrid_search(
        self, collection: str, query: str, k: int = 5, bm25_weight: float = 0.5
    ) -> list[dict]:
        """混合检索:BM25(关键词精确,如 API 名)+ 向量语义,加权融合。

        BM25 基于库内已存文本即时重建(库规模小,全量重建可接受;规模增大后应
        改为持久化 BM25 索引)。融合语义与官方 EnsembleRetriever 一致:加权
        倒数排名融合(weighted RRF),score(doc) = Σ_i w_i / (rank_i(doc) + c),
        c=60,rank 从 1 起,权重 [bm25_weight, 1 - bm25_weight](API 参考库调用
        方传 bm25_weight=0.7 让精确 API 名优先)。
        返回 [{text, score, metadata}];score 为融合得分,仅相对可比,非概率。
        """
        if not 0.0 <= bm25_weight <= 1.0:
            raise ValueError(f"bm25_weight 必须在 [0, 1] 范围内,实际为 {bm25_weight}")
        store = self._store(collection)
        all_data = store.get()  # 公开 API:{"ids","documents","metadatas",...}
        texts = all_data.get("documents") or []
        if not texts:
            return []
        ids = all_data.get("ids") or []
        metadatas = all_data.get("metadatas") or []
        by_id = {doc_id: (text, meta) for doc_id, text, meta in zip(ids, texts, metadatas)}
        # BM25 通道(API 名精确匹配);向量通道 query 结果 Document 携带 chroma id
        bm25 = _BM25Ranker(texts)
        bm25_ranked = [ids[pos] for pos, _ in bm25.search(query, k)]
        vec_ranked = [d.id for d, _ in store.similarity_search_with_score(query, k=k)]
        fused = _weighted_rrf([bm25_ranked, vec_ranked], [bm25_weight, 1 - bm25_weight])
        results = []
        for doc_id, score in fused[:k]:
            if doc_id not in by_id:
                continue
            text, meta = by_id[doc_id]
            results.append({"text": text, "score": score, "metadata": meta})
        return results


class _BM25Ranker:
    """Okapi BM25 轻量内建实现(等价官方 BM25Retriever;该依赖未随本环境安装)。

    分词:ASCII 单词(小写,含下划线;API 名按点拆分后逐段匹配)+ CJK 单字。
    公式:score(d) = Σ_t idf(t) * tf(t,d) * (k1+1) / (tf(t,d) + k1*(1-b+b*|d|/avgdl)),
    idf(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5)),k1=1.5、b=0.75(与
    langchain_community.retrievers.BM25Retriever 默认一致)。
    """

    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self._tokenized = [self._tokenize(t) for t in texts]
        self._n = len(self._tokenized)
        self._doc_lens = [len(toks) for toks in self._tokenized]
        self._avgdl = sum(self._doc_lens) / self._n if self._n else 0.0
        # 词项 -> 包含该词的文档数(文档频率)
        self._df: dict[str, int] = defaultdict(int)
        for toks in self._tokenized:
            for term in set(toks):
                self._df[term] += 1

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        text = text.lower()
        return re.findall(r"[a-z0-9_]+", text) + re.findall(r"[一-鿿]", text)

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        """返回 [(文档位置, BM25 得分)] 降序 top-k(仅含得分 > 0 的命中)。"""
        terms = set(self._tokenize(query))
        if not terms or self._n == 0:
            return []
        scores = [0.0] * self._n
        for term in terms:
            df = self._df.get(term, 0)
            idf = log(1 + (self._n - df + 0.5) / (df + 0.5))
            for i, toks in enumerate(self._tokenized):
                tf = toks.count(term)
                if not tf:
                    continue
                denom = tf + self.k1 * (1 - self.b + self.b * self._doc_lens[i] / self._avgdl)
                scores[i] += idf * tf * (self.k1 + 1) / denom
        ranked = sorted(range(self._n), key=lambda i: scores[i], reverse=True)
        return [(pos, scores[pos]) for pos in ranked if scores[pos] > 0][:k]


def _weighted_rrf(
    ranked_ids: list[list[str]], weights: list[float], c: float = 60.0
) -> list[tuple[str, float]]:
    """加权倒数排名融合(weighted RRF),与官方 EnsembleRetriever 融合语义一致:
    score(doc) = Σ_i w_i / (rank_i(doc) + c),rank 从 1 起,c 默认 60。
    返回 [(doc_id, 融合得分)] 降序。
    """
    fused: dict[str, float] = defaultdict(float)
    for docs, w in zip(ranked_ids, weights):
        for rank, doc_id in enumerate(docs, start=1):
            if doc_id is not None:
                fused[doc_id] += w / (rank + c)
    return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)


class StandardsLoader:
    """规范库:纯 markdown 整库注入,超预算自动转检索标注。"""

    def __init__(self, standards_dir: Path):
        self.standards_dir = Path(standards_dir)

    def load_all(self) -> list[str]:
        if not self.standards_dir.exists():
            return []
        return [
            p.read_text(encoding="utf-8")
            for p in sorted(self.standards_dir.glob("*.md"), key=lambda p: p.name)
        ]

    def inject_text(self, limit_tokens: int = 8000) -> str:
        files = self.load_all()
        if not files:
            return ""
        # token 估算:中文 ~1.5 字/token(乘 2/3 对齐);ASCII 4 字符/token 属偏保守
        budget = limit_tokens
        parts, used = [], 0
        for i, content in enumerate(files):
            est = len(content) * 2 // 3
            if used + est > budget:
                remaining = len(files) - i
                parts.append(f"[已截断,剩余 {remaining} 个文件请检索]")
                break
            parts.append(content)
            used += est
        return "\n\n---\n\n".join(parts)

    def search(self, query: str, k: int = 3) -> list[str]:
        """超限降级:复用 RagClient.guide 检索,返回命中文本列表。"""
        return [h["text"] for h in RagClient().search("guide", query, k=k)]
