"""法条库:Chroma 向量检索(语义定位)+ 源文件精确核验(反幻觉)。

双存储,职责分离(设计 §4.2):
  1. 向量库:collection `contract_law`,法条按"条"粒度 embedding 入库
     (每条款一条向量),语义检索用(审核节点按域过滤 + BM25/向量混合)。
  2. 源文件精确索引:seed 时按 law_name 建立 {article_no: 原文} 内存索引,
     校验层按 law_name + article_no 取**精确原文**,不依赖向量近似。

为什么 Chroma 不是 MySQL:审核要找语义关联("违约金过高"→条款),向量+关键词
混合检索才找得到;MySQL 只能精确匹配。校验层读源文件保证引文精确,防幻觉。

集合名说明:基类 RagClient._store 仅放行 RAG_COLLECTIONS(api_ref/guide/
experience),法条库用独立集合 contract_law,故内建 _LawRagClient 子类放开
集合名校验(Chroma 构造/嵌入模型复用 common/rag.py,不引入新依赖)。

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md §4.2。
"""
from __future__ import annotations

from pathlib import Path

from langchain_chroma import Chroma

from agents.contract_review_agent.utils.law_parser import DOMAIN_ALIASES, parse_law_md
from common.rag import RagClient, _embedding_model

_COLLECTION = "contract_law"


class _LawRagClient(RagClient):
    """RagClient 子类:放行 contract_law 集合(基类只允许 api_ref/guide/experience)。

    contract 法条库与 RAG 四库物理隔离(独立 data_dir/独立集合),此处仅放开
    集合名校验;Chroma 构造参数与嵌入模型完全复用基类逻辑。
    """

    def _store(self, collection: str) -> Chroma:
        if collection not in self._stores:
            self._stores[collection] = Chroma(
                collection_name=collection,
                embedding_function=_embedding_model(),
                persist_directory=str(self.chroma_dir),
            )
        return self._stores[collection]


class LawStore:
    def __init__(self, data_dir: Path = Path("data/contract-rag")):
        self._client = _LawRagClient(data_dir)
        self._exact: dict[str, dict[str, str]] = {}  # law_name -> {article_no: text}
        self._domains: dict[str, str] = {}  # law_name -> domain(领域硬过滤用)

    def _law_names(self, contract_type: str) -> list[str]:
        domain = DOMAIN_ALIASES.get(contract_type, "")
        if not domain:
            return []
        laws = self.list_laws()
        return [l["law_name"] for l in laws if l["domain"] == domain]

    def seed(self, md_text: str) -> dict:
        articles, meta = parse_law_md(md_text)
        law_name = meta["law_name"]
        # 首版不保证幂等:RagClient 未暴露 delete,重复 seed 会追加向量(重复灌库
        # 由 seed 脚本跑一次规避;条号重复覆盖语义在 _exact 内存索引上保证)。
        docs = [a.text for a in articles]
        metas = [a.model_dump() | {"id": f"{law_name}:{a.article_no}"} for a in articles]
        if docs:
            self._client.add_documents(_COLLECTION, docs, metas)
        self._exact[law_name] = {a.article_no: a.text for a in articles}
        self._domains[law_name] = meta["domain"]
        return {"law_name": law_name, "count": len(articles), "errors": meta["errors"]}

    def retrieve(self, query: str, contract_type: str = "", k: int = 5) -> list[dict]:
        names = self._law_names(contract_type)
        if not names:
            return self._client.hybrid_search(_COLLECTION, query, k=k)
        results: list[dict] = []
        for name in names:
            results += self._client.hybrid_search(
                _COLLECTION, query, k=k, filter={"law_name": name})
        # hybrid_search 得分为加权 RRF 融合,**越大越相关**,降序取 top-k
        results.sort(key=lambda d: d["score"], reverse=True)
        return results[:k]

    def verify_ref(self, law_name: str, article_no: str) -> str | None:
        return self._exact.get(law_name, {}).get(article_no)

    def list_laws(self) -> list[dict]:
        return [
            {"law_name": name, "domain": domain,
             "count": len(articles), "source_url": next(iter(articles.values()), "")}
            for name, domain, articles in self._summarize()
        ]

    def _summarize(self) -> list[tuple[str, str, dict]]:
        out = []
        for name, articles in self._exact.items():
            domain = self._domains.get(name, "")
            out.append((name, domain, articles))
        return out

    def search(self, query, k, filter=None):
        return self._client.hybrid_search(_COLLECTION, query, k=k, filter=filter)
