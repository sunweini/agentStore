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

# 嵌入服务单批上限 32(实测 >32 触发 413),取 16 留余量;超出/瞬态 424(CUDA OOM)
# 退避重试。见 seed() 分批逻辑。
_BATCH = 16
_MAX_ATTEMPTS = 6
_TRANSIENT_STATUS = (413, 424, 429, 500, 502, 503, 504)


def _retryable(exc: Exception) -> bool:
    """瞬态嵌入错误可重试:HTTP 状态码 413/424/429/5xx,或连接层失败。

    永久错误(校验/结构类)不带这些状态码,立即上抛不掩蔽。
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int) and status in _TRANSIENT_STATUS:
        return True
    import httpx  # 延迟导入:仅在异常分支走网络判定
    return isinstance(exc, httpx.TransportError)


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
    def __init__(self, data_dir: Path = Path("data/contract-rag"),
                 laws_dir: Path | None = None):
        """data_dir 管向量库;laws_dir 给定时构造即 load_bundled(填 _exact 精确索引)。

        laws_dir 用于生产运行时(API/run_review)加载内置权威法条源:校验层
        verify_ref 只依赖 _exact,不依赖向量灌库,杜绝"未 seed 时校验层全降级
        引用未能核验"的运行时空窗(审查 Critical #1)。
        """
        self._client = _LawRagClient(data_dir)
        self._exact: dict[str, dict[str, str]] = {}  # law_name -> {article_no: text}
        self._domains: dict[str, str] = {}  # law_name -> domain(领域硬过滤用)
        if laws_dir is not None:
            self.load_bundled(laws_dir)

    def load_bundled(self, laws_dir: Path) -> dict[str, dict]:
        """从内置法条源目录 data/laws/*.md 加载精确索引(_exact/_domains),不灌向量。

        逐条 parse_law_md(条号+原文+来源/领域),校验层按 law_name + article_no
        取逐字原文,与 seed() 灌库共用同一解析入口,保证"索引原文 == 权威源文本"。
        """
        loaded: dict[str, dict] = {}
        for md_path in sorted(laws_dir.glob("*.md")):
            articles, meta = parse_law_md(md_path.read_text(encoding="utf-8"))
            self._exact[meta["law_name"]] = {a.article_no: a.text for a in articles}
            self._domains[meta["law_name"]] = meta["domain"]
            loaded[meta["law_name"]] = {"count": len(articles), "errors": meta["errors"]}
        return loaded

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
            self._add_batched(docs, metas)
        self._exact[law_name] = {a.article_no: a.text for a in articles}
        self._domains[law_name] = meta["domain"]
        return {"law_name": law_name, "count": len(articles), "errors": meta["errors"]}

    def _add_batched(self, docs: list[str], metas: list[dict]) -> None:
        """分批 add_documents(嵌入服务单批上限 32,>32 触发 413)+ 瞬态退避重试。

        每批 ≤ _BATCH 条;超限 413 / CUDA OOM 424 / 5xx / 连接失败按
        0.5/1/2/4/8s 指数退避重试(共 _MAX_ATTEMPTS 次),永久错误立即上抛。
        """
        import time
        for start in range(0, len(docs), _BATCH):
            batch_docs = docs[start:start + _BATCH]
            batch_metas = metas[start:start + _BATCH]
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    self._client.add_documents(_COLLECTION, batch_docs, batch_metas)
                    break
                except Exception as exc:
                    if attempt >= _MAX_ATTEMPTS - 1 or not _retryable(exc):
                        raise
                    time.sleep(min(0.5 * 2 ** attempt, 8))

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
