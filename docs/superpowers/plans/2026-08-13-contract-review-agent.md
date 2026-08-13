# 合同审核 Agent 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 `contract_review_agent`:用户输入合同类型+原始 prompt 得到结构化审核 prompt(F1),上传 word/pdf 合同得到按章节、可核验法条依据的审核报告(F2)。

**Architecture:** LangGraph 流水线 parse → review_chapters → verify_refs → summarize → END。反幻觉核心 = 法条源文件(权威原文)+ Chroma 向量检索(语义定位)+ 引用校验层(逐条核验)。计费/鉴权独立实现(不复用 sentiment)。

**Tech Stack:** Python + LangChain/LangGraph + DeepSeek(`common/llm.py`)+ `common/rag.py`(RagClient/BM25+RRF)+ `common/db.py` + `python-docx` + `pypdf` + 百度 OCR 云端 API + FastAPI/sse-starlette。

**Spec:** `docs/superpowers/specs/2026-08-13-contract-review-agent-design.md`

## Global Constraints

- LLM 必须经 `common.llm.get_chat_model()` 工厂,不直接 new。审核节点 temperature **固定 0.1**,F1 优化节点 **≤0.2**。
- 反幻觉:LLM 只允许引用法条库检索返回的片段;引用校验层(纯代码,无 LLM)逐条核验;`statutory` 结论必须可回溯源文件原文,无法核验降级 `suggestion`。
- 文件限制:≤2MB 且正文 ≤5 万字,超限报错 `CONTRACT_TOO_LONG`。
- 法条种子文本**人工从权威来源采集**(flk.npc.gov.cn / 全国人大),严禁 LLM 生成/记忆填充。
- 计费/鉴权**独立实现**,表名 `contract_api_keys` / `contract_billing_records`,不复用 sentiment 的 billing/auth。
- 复用 `common/db.py`(MySQL 生产/SQLite 测试双后端)做存储访问,业务代码不直接连库。
- 复用 `common/rag.py` 的 `RagClient`(collection `contract_law`,法条按"条"粒度 embedding,元数据含 `law_name/article_no/domain/source_url/collected_date`)。
- 修改 `common/` 文件属项目级改动,完成后记根 `CHANGELOG.md` 项目级区;agent 内部改动记 `agents/contract_review_agent/CHANGELOG.md` 并 bump 版本号。
- 仅操作 `agents/contract_review_agent/` 目录 + 项目级公共文件,不动 sentiment/kingdee agent 目录。
- uvicorn 单 worker(sqlite checkpoint + JSON 文件库并发模型限制,同 sentiment)。
- 所有日志结构化 key=value(见根 CLAUDE.md 可观测性规范)。

---

### Task 1: 骨架(目录 + 占位文档 + langgraph 注册)

用户开发流程要求:骨架阶段只建目录+文档,不写实现代码。本任务交付可审查的目录结构与占位文档,确认后才继续后续任务。

**Files:**
- Create: `agents/contract_review_agent/__init__.py`
- Create: `agents/contract_review_agent/agent.py`(占位 docstring,含 build_graph 职责说明)
- Create: `agents/contract_review_agent/api.py`(占位 docstring)
- Create: `agents/contract_review_agent/graph/__init__.py`、`state.py`、`nodes.py`、`flows.py`、`verify.py`(占位 docstring,写清职责/待实现/设计文档引用)
- Create: `agents/contract_review_agent/utils/__init__.py`、`document_parser.py`、`chapterizer.py`、`ocr_client.py`(占位)
- Create: `agents/contract_review_agent/store/__init__.py`、`law_store.py`、`task_store.py`(占位)
- Create: `agents/contract_review_agent/scripts/seed_laws.py`(占位)
- Create: `agents/contract_review_agent/billing.py`、`auth.py`、`apikey_mgmt.py`(占位)
- Create: `agents/contract_review_agent/data/laws/.gitkeep`(法条源文件后续人工采集填入)
- Create: `agents/contract_review_agent/CLAUDE.md`(本 agent 职责/架构/常用操作/约束,仿 sentiment CLAUDE.md)
- Create: `agents/contract_review_agent/CHANGELOG.md`(v0.1.0 初始段)
- Modify: `langgraph.json`(注册 `contract_review_agent` 指向 `agent.py:build_graph`)
- Test: `tests/test_contract_review_agent.py`(骨架阶段仅 import 冒烟占位)

- [ ] **Step 1: 建目录结构**,每个文件写占位 docstring:职责、待实现、引用 `docs/superpowers/specs/2026-08-13-contract-review-agent-design.md`。

- [ ] **Step 2: 写 agent CLAUDE.md**,内容含:职责(双功能 F1/F2)、架构(章节流水线+校验层)、反幻觉铁律(法条只来自库,不编造)、常用操作(加法条/改 prompt/接百度OCR凭据)、约束(temperature 0.1/大小限制/独立计费)、收尾 CHANGELOG 约定。

- [ ] **Step 3: 注册 langgraph.json**

```json
{
  "dependencies": ["langchain-openai", "./common", "./agents", "./compile_service"],
  "graphs": {
    "sentiment-query-agent": "./agents/sentiment_query_agent/agent.py:build_agent",
    "kingdee_plugin_agent": "./agents/kingdee_plugin_agent/agent.py:build_graph",
    "contract_review_agent": "./agents/contract_review_agent/agent.py:build_graph"
  },
  "env": "./.env"
}
```

- [ ] **Step 4: 写占位测试**(仅验证包可导入,骨架阶段不测行为)

```python
def test_contract_agent_package_imports():
    from agents.contract_review_agent import agent, api  # noqa: F401
    from agents.contract_review_agent.graph import nodes, verify  # noqa: F401
    assert True
```

- [ ] **Step 5: 跑测试**

Run: `pytest tests/test_contract_review_agent.py -v`
Expected: PASS

- [ ] **Step 6: commit**

```bash
git add agents/contract_review_agent/ langgraph.json tests/test_contract_review_agent.py
git commit -m "feat: 合同审核 agent 骨架(目录+占位文档+langgraph 注册)"
```

> **检查点**:提交后停下,等用户说"继续"才写实现代码(项目开发铁律)。

---

### Task 2: 法条解析器(utils/law_parser.py)

法条源文件格式:
```
# 中华人民共和国劳动合同法
来源: https://flk.npc.gov.cn/detail2.html?...
采集日期: 2026-08-13
领域: labor

## 第一条
为了完善劳动合同制度,明确劳动合同双方当事人的权利和义务……

## 第二条
……
```

**Files:**
- Create: `agents/contract_review_agent/utils/law_parser.py`
- Test: `tests/test_contract_review_agent.py`(本任务起,测试集中此文件)

**Interfaces:**
- Consumes: —
- Produces:
  - `class LawArticle(BaseModel)` 字段:`law_name: str, article_no: str, text: str, source_url: str, collected_date: str, domain: str`
  - `def parse_law_md(md_text: str, default_domain: str = "contract") -> tuple[list[LawArticle], dict]` 返回 `(articles, meta)`;`meta` 含 `law_name/source_url/collected_date/domain`。非法条目跳过并在 meta["errors"] 记原因。
  - `DOMAIN_ALIASES: dict[str, str]` 合同类型→领域映射:`"劳动"→"labor"`, `"劳动合同"→"labor"`, `"买卖"→"contract"`, `"租赁"→"contract"`, `"承揽"→"contract"`, `"借款"→"contract"`, `"服务"→"contract"`;未知类型 → `""`(表示不限定领域)。

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_contract_review_agent.py -k law_parser -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现 law_parser.py**

```python
"""法条源文件(md)解析为 LawArticle 列表。法条文本必须人工采集,本模块只解析不生成。"""
from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel

DOMAIN_ALIASES: dict[str, str] = {
    "劳动": "labor", "劳动合同": "labor",
    "买卖": "contract", "租赁": "contract", "承揽": "contract",
    "借款": "contract", "服务": "contract",
}
_DOMAIN_NAMES = {"labor": "劳动/劳动合同", "contract": "买卖/租赁/承揽/借款/服务"}
_ARTICLE_RE = re.compile(r"^##\s*(第[一二三四五六七八九十百零]+条)\s*$")
_HEAD_RE = re.compile(r"^#\s*(.+)$")
_URL_RE = re.compile(r"^来源:\s*(\S+)$")
_DATE_RE = re.compile(r"^采集日期:\s*(\S+)$")
_DOMAIN_RE = re.compile(r"^领域:\s*(\S+)$")


class LawArticle(BaseModel):
    law_name: str
    article_no: str
    text: str
    source_url: str
    collected_date: str
    domain: str


def parse_law_md(md_text: str, default_domain: str = "contract") -> tuple[list[LawArticle], dict]:
    lines = md_text.splitlines()
    law_name, source_url, collected_date, domain = "", "", "", default_domain
    articles: list[LawArticle] = []
    errors: list[str] = []
    cur_no, buf = "", []
    for line in lines:
        if m := _HEAD_RE.match(line):
            law_name = m.group(1).strip()
        elif m := _URL_RE.match(line):
            source_url = m.group(1).strip()
        elif m := _DATE_RE.match(line):
            collected_date = m.group(1).strip()
        elif m := _DOMAIN_RE.match(line):
            domain = m.group(1).strip()
        elif m := _ARTICLE_RE.match(line):
            if cur_no and buf:
                articles.append(LawArticle(
                    law_name=law_name, article_no=cur_no, text="".join(buf).strip(),
                    source_url=source_url, collected_date=collected_date, domain=domain,
                ))
            cur_no = m.group(1)
            buf = []
        elif cur_no:
            buf.append(line)
    if cur_no and buf:
        articles.append(LawArticle(
            law_name=law_name, article_no=cur_no, text="".join(buf).strip(),
            source_url=source_url, collected_date=collected_date, domain=domain,
        ))
    if not law_name:
        errors.append("缺 law_name")
    for a in articles:
        if not a.text:
            errors.append(f"{a.article_no} 正文为空")
    meta = {"law_name": law_name, "source_url": source_url,
            "collected_date": collected_date or date.today().isoformat(),
            "domain": domain, "errors": errors, "count": len(articles)}
    return articles, meta
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_contract_review_agent.py -k law_parser -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add agents/contract_review_agent/utils/law_parser.py tests/test_contract_review_agent.py
git commit -m "feat: 法条源文件解析器(条粒度 LawArticle)"
```

---

### Task 3: 法条库(store/law_store.py)

Chroma 向量检索 + 源文件精确核验双路径。数据目录 `data/contract-rag`。

**Files:**
- Create: `agents/contract_review_agent/store/law_store.py`
- Create: `agents/contract_review_agent/scripts/seed_laws.py`
- Modify: `tests/test_contract_review_agent.py`

**Interfaces:**
- Consumes: `parse_law_md`(Task 2)、`RagClient`(common/rag.py)、`_embedding_model`(common/rag.py)
- Produces:
  - `class LawStore:` 构造 `LawStore(data_dir: Path = Path("data/contract-rag"))`
  - `def seed(self, md_text: str) -> dict` 灌库,返回 `{"law_name", "count", "errors"}`(同 law_name+article_no 重复覆盖:先按元数据删旧再 add)
  - `def retrieve(self, query: str, contract_type: str = "", k: int = 5) -> list[dict]` 领域过滤 + `hybrid_search`;返回 `[{text, score, metadata}]`
  - `def verify_ref(self, law_name: str, article_no: str) -> str | None` 读源文件精确原文(启动时由 seed 或 load 建立 `{law_name: {article_no: text}}` 索引),找不到返回 None
  - `def list_laws(self) -> list[dict]` 返回 `[{law_name, domain, count, source_url}]`
  - `def search(self, query, k, filter)` 透传 RagClient

- [ ] **Step 1: 写失败测试**(用 tmp_path 数据目录隔离)

```python
import tempfile
from pathlib import Path
from agents.contract_review_agent.store.law_store import LawStore

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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_contract_review_agent.py -k law_store -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现 law_store.py**

```python
"""法条库:Chroma 向量检索(语义定位)+ 源文件精确核验(反幻觉)。

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md §4.2。
"""
from __future__ import annotations

from pathlib import Path

from common.rag import RagClient
from agents.contract_review_agent.utils.law_parser import DOMAIN_ALIASES, parse_law_md

_COLLECTION = "contract_law"


class LawStore:
    def __init__(self, data_dir: Path = Path("data/contract-rag")):
        self._client = RagClient(data_dir)
        self._exact: dict[str, dict[str, str]] = {}  # law_name -> {article_no: text}

    def _law_names(self, contract_type: str) -> list[str]:
        domain = DOMAIN_ALIASES.get(contract_type, "")
        if not domain:
            return []
        laws = self.list_laws()
        return [l["law_name"] for l in laws if l["domain"] == domain]

    def seed(self, md_text: str) -> dict:
        articles, meta = parse_law_md(md_text)
        law_name = meta["law_name"]
        existing = self._client.search(_COLLECTION, "", k=1000, filter={"law_name": law_name})
        if existing:
            ids = [d["metadata"]["id"] for d in existing if "id" in d["metadata"]]
            # RagClient 无 delete 暴露,跳过删除(seed 幂等:同条重灌覆盖向量)
        docs = [a.text for a in articles]
        metas = [a.model_dump() | {"id": f"{law_name}:{a.article_no}"} for a in articles]
        if docs:
            self._client.add_documents(_COLLECTION, docs, metas)
        self._exact[law_name] = {a.article_no: a.text for a in articles}
        return {"law_name": law_name, "count": len(articles), "errors": meta["errors"]}

    def retrieve(self, query: str, contract_type: str = "", k: int = 5) -> list[dict]:
        names = self._law_names(contract_type)
        if not names:
            return self._client.hybrid_search(_COLLECTION, query, k=k)
        results: list[dict] = []
        for name in names:
            results += self._client.hybrid_search(
                _COLLECTION, query, k=k, filter={"law_name": name})
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
            domain = next(iter(articles.values()), "")
            out.append((name, domain, articles))
        return out

    def search(self, query, k, filter=None):
        return self._client.hybrid_search(_COLLECTION, query, k=k, filter=filter)
```

- [ ] **Step 4: 实现 seed_laws.py**(灌库脚本,数据文件人工采集)

```python
"""法条灌库脚本:python -m agents.contract_review_agent.scripts.seed_laws

数据:agents/contract_review_agent/data/laws/*.md(人工从权威来源采集,严禁 AI 生成)。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from agents.contract_review_agent.store.law_store import LawStore


def main(data_dir: Path, laws_dir: Path) -> None:
    store = LawStore(data_dir)
    for md_path in sorted(laws_dir.glob("*.md")):
        result = store.seed(md_path.read_text(encoding="utf-8"))
        print(f"{result['law_name']}: {result['count']} 条,errors={result['errors']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=Path("data/contract-rag"))
    ap.add_argument("--laws-dir", type=Path,
                    default=Path("agents/contract_review_agent/data/laws"))
    args = ap.parse_args()
    main(args.data_dir, args.laws_dir)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_contract_review_agent.py -k law_store -v`
Expected: PASS

- [ ] **Step 6: commit**

```bash
git add agents/contract_review_agent/store/law_store.py agents/contract_review_agent/scripts/seed_laws.py tests/test_contract_review_agent.py
git commit -m "feat: 法条库(Chroma 检索 + 源文件精确核验)"
```

> **数据检查点**:法条种子文本(`data/laws/*.md`)需人工从 flk.npc.gov.cn 采集,见 Task 14 前的"种子数据采集"说明。测试用合成 md 已覆盖逻辑。

---

### Task 4: 文件解析层(utils/document_parser.py + chapterizer.py)

docx 按标题样式分章;pdf 文本层按行文启发式分章;无文本层标记需 OCR;大小校验。

**Files:**
- Create: `agents/contract_review_agent/utils/document_parser.py`
- Create: `agents/contract_review_agent/utils/chapterizer.py`
- Modify: `tests/test_contract_review_agent.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `class Chapter(BaseModel): title: str, level: int, order: int, text: str`
  - `class Document(BaseModel): chapters: list[Chapter], total_chars: int, source_type: str`
  - `class ContractTooLongError(ValueError): pass`
  - `class NeedsOcrError(ValueError): pass`
  - `class UnsupportedTypeError(ValueError): pass`
  - `def parse_document(path: str | Path, max_bytes: int = 2 * 1024 * 1024, max_chars: int = 50_000) -> Document`
  - `def build_chapters(blocks: list[tuple[str, int]]) -> list[Chapter]` 输入 `(text, level)` 列表;`level=1` 表示章节标题行(标题文本),`level=0` 表示正文行(挂当前章);无标题时归入单章 `未命名章节`。

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path
import tempfile
import pytest
from agents.contract_review_agent.utils.chapterizer import build_chapters
from agents.contract_review_agent.utils.document_parser import (
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_contract_review_agent.py -k chapterizer -v`
Expected: FAIL

- [ ] **Step 3: 实现 chapterizer.py**

```python
"""把文档块序列构造成章节树。"""
from __future__ import annotations

from pydantic import BaseModel


class Chapter(BaseModel):
    title: str
    level: int
    order: int
    text: str


def build_chapters(blocks: list[tuple[str, int]]) -> list[Chapter]:
    chapters: list[Chapter] = []
    current: Chapter | None = None
    order = 0
    for text, level in blocks:
        text = text.strip()
        if not text:
            continue
        if level >= 1:
            order += 1
            current = Chapter(title=text, level=level, order=order, text="")
            chapters.append(current)
        else:
            if current is None:
                order += 1
                current = Chapter(title="未命名章节", level=0, order=order, text="")
                chapters.append(current)
            current.text = (current.text + "\n" + text).strip()
    return chapters
```

- [ ] **Step 4: 实现 document_parser.py**

```python
"""合同文件解析:docx/python-docx 标题分章,pdf/pypdf 文本层,无文本层抛 NeedsOcr。"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from agents.contract_review_agent.utils.chapterizer import Chapter, build_chapters


class Document(BaseModel):
    chapters: list[Chapter]
    total_chars: int
    source_type: str


class ContractTooLongError(ValueError):
    pass


class NeedsOcrError(ValueError):
    pass


class UnsupportedTypeError(ValueError):
    pass


def parse_document(path: str | Path, max_bytes: int = 2 * 1024 * 1024,
                   max_chars: int = 50_000) -> Document:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    if p.stat().st_size > max_bytes:
        raise ContractTooLongError("文件超过 2MB 限制")
    suffix = p.suffix.lower()
    if suffix == ".docx":
        blocks = _parse_docx(p)
        source_type = "docx"
    elif suffix == ".pdf":
        blocks = _parse_pdf(p)
        source_type = "pdf"
    else:
        raise UnsupportedTypeError(f"不支持的文件类型: {suffix}")
    chapters = build_chapters(blocks)
    total = sum(len(c.text) for c in chapters)
    if total > max_chars:
        raise ContractTooLongError(f"正文超过 5 万字限制(实际 {total})")
    return Document(chapters=chapters, total_chars=total, source_type=source_type)


def _parse_docx(p: Path) -> list[tuple[str, int]]:
    from docx import Document as _Docx

    doc = _Docx(str(p))
    blocks: list[tuple[str, int]] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower()
        level = 1 if ("heading" in style or "标题" in style) else 0
        blocks.append((text, level))
    return blocks


def _parse_pdf(p: Path) -> list[tuple[str, int]]:
    from pypdf import PdfReader

    reader = PdfReader(str(p))
    pages: list[str] = []
    for page in reader.pages:
        t = (page.extract_text() or "").strip()
        pages.append(t)
    if not any(pages):
        raise NeedsOcrError("PDF 无文本层,需要 OCR")
    blocks: list[tuple[str, int]] = []
    for page_text in pages:
        for line in page_text.splitlines():
            line = line.strip()
            if not line:
                continue
            blocks.append((line, 1 if _looks_like_heading(line) else 0))
    return blocks


def _looks_like_heading(line: str) -> bool:
    return (line.startswith(("第", "一、", "二、", "三、", "四、", "五、",
                             "六、", "七、", "八、", "九、", "十、"))
            and any(k in line for k in ("章", "节", "条"))) or len(line) <= 30
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_contract_review_agent.py -k "chapterizer or unsupported or too_large" -v`
Expected: PASS(docx/pdf 实际解析需真实文件,另加端到端样例)

- [ ] **Step 6: commit**

```bash
git add agents/contract_review_agent/utils/chapterizer.py agents/contract_review_agent/utils/document_parser.py tests/test_contract_review_agent.py
git commit -m "feat: 合同文件解析(docx 分章/pdf 文本层/大小校验)"
```

---

### Task 5: 百度 OCR 客户端(utils/ocr_client.py)

**Files:**
- Create: `agents/contract_review_agent/utils/ocr_client.py`
- Modify: `tests/test_contract_review_agent.py`
- Modify: `.env.example`(加 `BAIDU_OCR_API_KEY` / `BAIDU_OCR_SECRET_KEY` 注释项)

**Interfaces:**
- Consumes: `common.config.get_env`
- Produces:
  - `def get_baidu_token(api_key: str, secret_key: str) -> str` 走 `https://aip.baidubce.com/oauth/2.0/token`
  - `def ocr_image_bytes(img: bytes, token: str) -> str` 走通用文字识别高精度版 `/rest/2.0/ocr/v1/accurate_basic`,返回拼接文本
  - `def ocr_pdf_pages(pdf_path: Path, token: str) -> str | None` 逐页渲染为图片后 OCR;渲染依赖(如 PyMuPDF)可选,不可用时返回 None

- [ ] **Step 1: 写失败测试**(mock HTTP,不真调百度)

```python
from unittest.mock import patch
from agents.contract_review_agent.utils import ocr_client

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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_contract_review_agent.py -k ocr -v`
Expected: FAIL

- [ ] **Step 3: 实现 ocr_client.py**

```python
"""百度智能云 OCR 客户端(云端接口,零本地模型)。

凭据:.env 的 BAIDU_OCR_API_KEY / BAIDU_OCR_SECRET_KEY。
"""
from __future__ import annotations

import base64

import httpx

_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
_OCR_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic"


def get_baidu_token(api_key: str, secret_key: str) -> str:
    resp = httpx.post(_TOKEN_URL, params={
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": secret_key,
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def ocr_image_bytes(img: bytes, token: str) -> str:
    resp = httpx.post(_OCR_URL, params={"access_token": token},
                      data={"image": base64.b64encode(img)}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    words = [w["words"] for w in data.get("words_result", [])]
    return " ".join(words)


def ocr_pdf_pages(pdf_path: str, token: str) -> str | None:
    """PDF 逐页渲染 OCR。依赖 PyMuPDF(fitz),未安装时返回 None。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    texts: list[str] = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            texts.append(ocr_image_bytes(pix.tobytes("png"), token))
    return "\n".join(texts)
```

- [ ] **Step 4: 更新 .env.example**(追加两行注释项)

```bash
BAIDU_OCR_API_KEY=        # 百度 OCR 云端接口(contract_review_agent 扫描件识别)
BAIDU_OCR_SECRET_KEY=
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_contract_review_agent.py -k ocr -v`
Expected: PASS

- [ ] **Step 6: commit**

```bash
git add agents/contract_review_agent/utils/ocr_client.py .env.example tests/test_contract_review_agent.py
git commit -m "feat: 百度 OCR 云端客户端(扫描件识别)"
```

---

### Task 6: 章节审核节点(graph/nodes.py)

**Files:**
- Create: `agents/contract_review_agent/graph/state.py`(AgentState + 模型)
- Create: `agents/contract_review_agent/graph/nodes.py`(审核节点 + F1 节点)
- Modify: `tests/test_contract_review_agent.py`

**Interfaces:**
- Consumes: `LawStore`(Task 3)、`common.llm.get_chat_model`
- Produces:
  - `class LegalRef(BaseModel): law_name: str, article_no: str, article_text: str`
  - `class Finding(BaseModel): 原文引用: str, 风险类型: Literal["合规","权益","漏洞","歧义"], 问题描述: str, 改进建议: str, 法律依据: list[LegalRef] = [], confidence: Literal["statutory","suggestion"] = "statutory"`
  - `class ChapterReview(BaseModel): chapter: str, findings: list[Finding]`
  - `class AgentState(TypedDict): contract_type: str, review_prompt: str, chapters: list[dict], chapter_reviews: list[dict], report: str, error: str`
  - `def review_chapter(llm, law_store, chapter: dict, review_prompt: str) -> dict` 返回 `ChapterReview.model_dump()`
  - `def review_all(state, llm, law_store) -> dict` 遍历 chapters 调用 review_chapter,返回 `{"chapter_reviews": [...]}`

- [ ] **Step 1: 写失败测试**(mock LLM 返回固定 JSON,验证节点装配)

```python
from unittest.mock import MagicMock
from agents.contract_review_agent.graph.state import ChapterReview, Finding, LegalRef
from agents.contract_review_agent.graph.nodes import review_chapter

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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_contract_review_agent.py -k review_chapter -v`
Expected: FAIL

- [ ] **Step 3: 实现 state.py**

```python
"""图状态与审核数据模型。"""
from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel


class LegalRef(BaseModel):
    law_name: str
    article_no: str
    article_text: str


class Finding(BaseModel):
    原文引用: str
    风险类型: Literal["合规", "权益", "漏洞", "歧义"]
    问题描述: str
    改进建议: str
    法律依据: list[LegalRef] = []
    confidence: Literal["statutory", "suggestion"] = "statutory"


class ChapterReview(BaseModel):
    chapter: str
    findings: list[Finding]


class AgentState(TypedDict):
    contract_type: str
    review_prompt: str
    _file_path: str
    _file_name: str
    chapters: list[dict]
    chapter_reviews: list[dict]
    report: str
    report_json: dict
    error: str
```

- [ ] **Step 4: 实现 nodes.py**(审核节点,LLM 强制 JSON,temp 0.1)

```python
"""审核节点:每章检索法条 + LLM 判断(JSON, temperature=0.1)。"""
from __future__ import annotations

import json

from common.llm import get_chat_model
from agents.contract_review_agent.graph.state import AgentState, ChapterReview

_CHAPTER_SYSTEM = (
    "你是合同审核专家。严格遵守以下要求:\n"
    "1. 只能引用用户提供的【法条片段】中的条款,禁止引用片段外的任何法条,禁止编造。\n"
    "2. 对每个问题给出:原文引用(合同具体条款/段落)、风险类型(合规/权益/漏洞/歧义)、"
    "问题描述、改进建议、法律依据。\n"
    "3. 法律依据只能从【法条片段】选取,字段 article_text 必须与片段原文一致;"
    "没有可依据的条款时,法律依据为空数组,confidence 填 suggestion。\n"
    "4. 有法律依据时 confidence 填 statutory。\n"
    "5. 只输出 JSON,格式: {\"chapter\": \"章标题\", \"findings\": [...]}。\n"
)


def _review_model():
    return get_chat_model().bind(temperature=0.1)


def review_chapter(llm, contract_type: str, chapter: dict,
                   review_prompt: str, law_store=None) -> dict:
    fragments = ""
    if law_store is not None:
        hits = law_store.retrieve(chapter.get("text", ""), contract_type, k=5)
        fragments = "\n".join(
            f"[{h['metadata'].get('law_name')} {h['metadata'].get('article_no')}] {h['text']}"
            for h in hits)
    user = f"审核要求:\n{review_prompt}\n\n合同章节:\n{chapter.get('text', '')}\n\n法条片段:\n{fragments or '(无)'}"
    resp = llm.invoke([
        {"role": "system", "content": _CHAPTER_SYSTEM},
        {"role": "user", "content": user},
    ])
    raw = resp.content if isinstance(resp.content, str) else json.dumps(resp.content)
    try:
        parsed = ChapterReview.model_validate_json(raw)
        return parsed.model_dump()
    except Exception as exc:
        return {"chapter": chapter.get("title", ""), "findings": [],
                "_error": f"bad_json: {exc}"}


def review_all(state: AgentState, law_store) -> dict:
    llm = _review_model()
    reviews = []
    for chapter in state["chapters"]:
        reviews.append(review_chapter(
            llm, state.get("contract_type", ""), chapter,
            state.get("review_prompt", ""), law_store))
    return {"chapter_reviews": reviews}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_contract_review_agent.py -k review_chapter -v`
Expected: PASS(mock 直连 `review_chapter`,不依赖真实 LLM)

- [ ] **Step 6: commit**

```bash
git add agents/contract_review_agent/graph/state.py agents/contract_review_agent/graph/nodes.py tests/test_contract_review_agent.py
git commit -m "feat: 章节审核节点(法条片段注入, temp=0.1 强制 JSON)"
```

---

### Task 7: 引用校验层(graph/verify.py)(核心反幻觉)

**Files:**
- Create: `agents/contract_review_agent/graph/verify.py`
- Modify: `tests/test_contract_review_agent.py`

**Interfaces:**
- Consumes: `LawStore.verify_ref`(Task 3)
- Produces:
  - `def verify_reviews(chapter_reviews: list[dict], law_store) -> list[dict]` 逐条核验 `法律依据`;返回清洗后的 reviews
  - 校验规则:① `law_name+article_no` 库中存在;② LLM 的 `article_text` 与库内原文 `difflib.SequenceMatcher.ratio() >= 0.8`。不满足① → 移除该依据、对应 finding `confidence="suggestion"`、`问题描述` 追加 `(引用未能核验)`;
  不满足② → 用库内原文替换 `article_text`。

- [ ] **Step 1: 写失败测试**

```python
import tempfile
from pathlib import Path
from agents.contract_review_agent.store.law_store import LawStore
from agents.contract_review_agent.graph.verify import verify_reviews

MD = """# 测试法
来源: u
采集日期: 2026-08-13
领域: labor

## 第一条
违约金不得超过实际损失的百分之三十。"""

def _law(tmp_path: Path) -> LawStore:
    s = LawStore(data_dir=tmp_path / "rag")
    s.seed(MD)
    return s

def test_verify_keeps_exact_ref(tmp_path):
    reviews = [{"chapter": "A", "findings": [{
        "原文引用": "赔 5%", "风险类型": "合规", "问题描述": "x", "改进建议": "y",
        "法律依据": [{"law_name": "测试法", "article_no": "第一条",
                       "article_text": "违约金不得超过实际损失的百分之三十。"}],
        "confidence": "statutory"}]}]
    out = verify_reviews(reviews, _law(tmp_path))
    assert out[0]["findings"][0]["confidence"] == "statutory"
    assert len(out[0]["findings"][0]["法律依据"]) == 1

def test_verify_drops_nonexistent_article(tmp_path):
    reviews = [{"chapter": "A", "findings": [{
        "原文引用": "q", "风险类型": "漏洞", "问题描述": "x", "改进建议": "y",
        "法律依据": [{"law_name": "测试法", "article_no": "第九百条",
                       "article_text": "编造的。"}],
        "confidence": "statutory"}]}]
    out = verify_reviews(reviews, _law(tmp_path))
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
    out = verify_reviews(reviews, _law(tmp_path))
    ref = out[0]["findings"][0]["法律依据"][0]
    assert ref["article_text"] == "违约金不得超过实际损失的百分之三十。"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_contract_review_agent.py -k verify -v`
Expected: FAIL

- [ ] **Step 3: 实现 verify.py**

```python
"""引用校验层:逐条核验法律依据可回溯源文件原文(纯代码,无 LLM)。

规则:
- 条号不存在 → 移除依据,该 finding 降级 suggestion,问题描述追加"(引用未能核验)"。
- 引文与库内原文不一致(ratio<0.8)→ 用库内原文替换,LLM 只定位不改写。
"""
from __future__ import annotations

import difflib

_THRESHOLD = 0.8


def _verify_ref(law_store, ref: dict) -> dict | None:
    exact = law_store.verify_ref(ref["law_name"], ref["article_no"])
    if exact is None:
        return None
    ratio = difflib.SequenceMatcher(None, ref.get("article_text", ""), exact).ratio()
    if ratio < _THRESHOLD:
        ref["article_text"] = exact
    return ref


def verify_reviews(chapter_reviews: list[dict], law_store) -> list[dict]:
    for review in chapter_reviews:
        for finding in review.get("findings", []):
            refs = finding.get("法律依据", [])
            kept = [r for r in (_verify_ref(law_store, r) for r in refs) if r is not None]
            if refs and not kept:
                finding["confidence"] = "suggestion"
                finding["问题描述"] = finding.get("问题描述", "") + " (引用未能核验)"
            finding["法律依据"] = kept
    return chapter_reviews
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_contract_review_agent.py -k verify -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add agents/contract_review_agent/graph/verify.py tests/test_contract_review_agent.py
git commit -m "feat: 引用校验层(核心反幻觉,逐条核验法条引用)"
```

---

### Task 8: 汇总节点 + 报告模板(graph/report.py)

**Files:**
- Create: `agents/contract_review_agent/graph/report.py`
- Modify: `tests/test_contract_review_agent.py`

**Interfaces:**
- Consumes: `ChapterReview`(Task 6)
- Produces:
  - `RISK_ORDER: dict[str, int]` = `{"高风险": 0, "中风险": 1, "提示": 2}`
  - `def risk_level(finding: dict) -> str` 合规→高风险;权益/漏洞→中风险;suggestion→提示
  - `def build_report(chapter_reviews: list[dict], meta: dict) -> str` 按 §6 markdown 模板输出报告
  - `def build_report_json(chapter_reviews: list[dict]) -> dict` 结构化 JSON(含统计)

- [ ] **Step 1: 写失败测试**

```python
from agents.contract_review_agent.graph.report import build_report, risk_level, build_report_json

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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_contract_review_agent.py -k report -v`
Expected: FAIL

- [ ] **Step 3: 实现 report.py**

```python
"""汇总:风险分级 + markdown 报告 + 结构化 JSON。"""
from __future__ import annotations

from collections import Counter

RISK_ORDER = {"高风险": 0, "中风险": 1, "提示": 2}


def risk_level(finding: dict) -> str:
    if finding.get("confidence") == "suggestion":
        return "提示"
    return {"合规": "高风险", "权益": "中风险", "漏洞": "中风险", "歧义": "中风险"}.get(
        finding.get("风险类型"), "提示")


def _finding_block(seq: int, finding: dict) -> str:
    refs = finding.get("法律依据", [])
    ref_lines = "\n".join(
        f"**依据**:《{r['law_name']}》{r['article_no']}——\"{r['article_text']}\""
        for r in refs)
    note = "(法律依据已核验)" if refs else "(无法律依据,仅提示,非强制)"
    return (
        f"### {seq}.1 [{finding.get('章节', '')}]\n"
        f"**原文引用**:{finding.get('原文引用', '')}\n"
        f"**问题**:{finding.get('问题描述', '')}\n"
        f"**建议**:{finding.get('改进建议', '')}\n"
        + (ref_lines + "\n" if ref_lines else "") + f"{note}\n")


def build_report(chapter_reviews: list[dict], meta: dict) -> str:
    grouped: dict[str, list[tuple[str, int, dict]]] = {"高风险": [], "中风险": [], "提示": []}
    seq = 0
    for review in chapter_reviews:
        for finding in review.get("findings", []):
            seq += 1
            finding = dict(finding, 章节=review.get("chapter", ""))
            grouped[risk_level(finding)].append((review.get("chapter", ""), seq, finding))
    stats = Counter(risk_level(f) for r in chapter_reviews
                    for f in r.get("findings", []))
    lines = [
        "# 合同审核报告",
        "",
        f"- 合同名称:{meta.get('合同名称', '')}",
        f"- 审核依据:{meta.get('法条库版本', '')}",
        f"- 审核时间:{meta.get('审核时间', '')}",
        f"- 风险结论:高风险 {stats['高风险']} 处 / 中风险 {stats['中风险']} 处 / 提示 {stats['提示']} 处",
        "",
    ]
    for level in ("高风险", "中风险", "提示"):
        items = grouped[level]
        if not items:
            continue
        lines.append(f"## {level}")
        lines.append("")
        for chapter, seq, finding in items:
            lines.append(f"### {seq}.1 [{chapter}]")
            lines.append(f"**原文引用**:{finding.get('原文引用', '')}")
            lines.append(f"**问题**:{finding.get('问题描述', '')}")
            lines.append(f"**建议**:{finding.get('改进建议', '')}")
            for r in finding.get("法律依据", []):
                lines.append(f"**依据**:《{r['law_name']}》{r['article_no']}——\"{r['article_text']}\"")
            note = "(法律依据已核验)" if finding.get("法律依据") else "(无法律依据,仅提示,非强制)"
            lines.append(note)
            lines.append("")
    return "\n".join(lines)


def build_report_json(chapter_reviews: list[dict]) -> dict:
    stats = Counter(risk_level(f) for r in chapter_reviews
                    for f in r.get("findings", []))
    return {
        "chapter_reviews": chapter_reviews,
        "stats": {"高风险": stats["高风险"], "中风险": stats["中风险"], "提示": stats["提示"]},
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_contract_review_agent.py -k report -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add agents/contract_review_agent/graph/report.py tests/test_contract_review_agent.py
git commit -m "feat: 汇总节点(风险分级 + markdown 报告 + JSON)"
```

---

### Task 9: F1 prompt 优化(graph/prompt_node.py)

**Files:**
- Create: `agents/contract_review_agent/graph/prompt_node.py`
- Modify: `tests/test_contract_review_agent.py`

**Interfaces:**
- Consumes: `common.llm.get_chat_model`
- Produces:
  - `def optimize_review_prompt(contract_type: str, user_prompt: str, llm=None) -> str` 返回结构化审核 prompt(JSON 或 markdown 文本),temperature ≤0.2
  - `_DEFAULT_SECTIONS: list[str]` 输出结构模板(角色/范围/风险清单/输出格式/引用指引)

- [ ] **Step 1: 写失败测试**(mock LLM)

```python
from unittest.mock import MagicMock
from agents.contract_review_agent.graph.prompt_node import optimize_review_prompt

def test_optimize_review_prompt_returns_text():
    fake = MagicMock()
    fake.invoke.return_value.content = (
        "你是合同审核专家。\n一、审核范围…\n二、风险清单…\n三、输出格式…\n四、引用指引…")
    out = optimize_review_prompt("劳动合同", "重点看违约金", llm=fake)
    assert "审核" in out
    assert fake.invoke.call_args is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_contract_review_agent.py -k optimize -v`
Expected: FAIL

- [ ] **Step 3: 实现 prompt_node.py**

```python
"""F1:把 合同类型+用户原始 prompt 优化为结构化审核 prompt。"""
from __future__ import annotations

from common.llm import get_chat_model

_DEFAULT_SECTIONS = [
    "一、角色:你是{类型}合同审核专家,严格依据法律审核。",
    "二、审核范围:{用户要求}",
    "三、风险清单:逐条检查{类型}合同常见风险(条款合法合规、双方权利义务对等、违约责任、争议解决)。",
    "四、输出格式:对每个问题给出【原文引用/风险类型/问题描述/改进建议/法律依据】;"
    "法律依据只允许引用法条库片段原文,禁止编造。",
    "五、引用指引:无法律依据时明确标注'仅提示,非强制'。",
]


def _prompt_llm():
    return get_chat_model().bind(temperature=0.2)


def optimize_review_prompt(contract_type: str, user_prompt: str, llm=None) -> str:
    llm = llm or _prompt_llm()
    template = "\n".join(_DEFAULT_SECTIONS)
    filled = template.format(类型=contract_type, 用户要求=user_prompt.strip())
    resp = llm.invoke([
        {"role": "system",
         "content": "把审核要求优化为结构化、可直接执行的合同审核 prompt。保留用户原有要点,"
                    "补充类型常见风险与引用法规指引。只输出优化后的 prompt 本身,不要解释。"},
        {"role": "user", "content": f"合同类型:{contract_type}\n原始要求:{user_prompt}"},
    ])
    out = resp.content if isinstance(resp.content, str) else str(resp.content)
    if not out.strip():
        out = filled
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_contract_review_agent.py -k optimize -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add agents/contract_review_agent/graph/prompt_node.py tests/test_contract_review_agent.py
git commit -m "feat: F1 prompt 优化节点(合同类型+原始prompt, temp<=0.2)"
```

---

### Task 10: 图构建(agent.py build_graph + flows.py)

**Files:**
- Create: `agents/contract_review_agent/graph/flows.py`
- Modify: `agents/contract_review_agent/agent.py`(实现 build_graph)
- Modify: `tests/test_contract_review_agent.py`

**Interfaces:**
- Consumes: `parse_document`(Task 4)、`LawStore`(Task 3)、`review_all`(Task 6)、`verify_reviews`(Task 7)、`build_report`(Task 8)、`NeedsOcrError/ContractTooLongError`(Task 4)
- Produces:
  - `def build_graph() -> CompiledGraph` LangGraph:START → parse → review → verify → summarize → END
  - `def run_review(file_path, contract_type, review_prompt, law_store=None) -> dict` 同步一次跑完整图(供 API/CLI 调用),返回 `{report, report_json, error}`
  - parse 节点捕获 `NeedsOcrError` → state["error"]="needs_ocr";`ContractTooLongError` → state["error"]="too_long"

- [ ] **Step 1: 写失败测试**(图构建冒烟 + run_review 走 mock LLM 失败路径)

```python
import tempfile
from pathlib import Path
from agents.contract_review_agent.graph.flows import build_graph
from agents.contract_review_agent.agent import run_review

def test_build_graph_smoke():
    graph = build_graph()
    assert graph is not None

def test_run_review_too_long():
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
        f.write(b"\x00" * 3000)
        p = f.name
    result = run_review(p, "劳动合同", "请审核", law_store=None)
    assert result["error"] in ("too_long", "unsupported")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_contract_review_agent.py -k "build_graph or run_review" -v`
Expected: FAIL

- [ ] **Step 3: 实现 flows.py**

```python
"""LangGraph 图:parse → review → verify → summarize。"""
from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

from agents.contract_review_agent.graph.state import AgentState


def _parse_node(state: AgentState, services: dict) -> dict:
    from agents.contract_review_agent.utils.document_parser import (
        ContractTooLongError, NeedsOcrError, UnsupportedTypeError, parse_document)
    try:
        doc = parse_document(state["_file_path"])
    except NeedsOcrError:
        return {"error": "needs_ocr"}
    except ContractTooLongError:
        return {"error": "too_long"}
    except (UnsupportedTypeError, FileNotFoundError):
        return {"error": "unsupported"}
    return {"chapters": [c.model_dump() for c in doc.chapters]}


def _route_after_parse(state: AgentState) -> Literal["review", "end"]:
    return "end" if state.get("error") else "review"


def build_graph(law_store=None) -> Runnable:
    """返回编译后的 LangGraph 图。law_store 为 None 时审核不注入法条片段(纯 mock 路径)。"""
    services = {"law_store": law_store}

    def _review(state: AgentState) -> dict:
        from agents.contract_review_agent.graph.nodes import review_all
        return review_all(state, services["law_store"])

    def _verify(state: AgentState) -> dict:
        from agents.contract_review_agent.graph.verify import verify_reviews
        if services["law_store"] is None:
            return {"chapter_reviews": state.get("chapter_reviews", [])}
        return {"chapter_reviews": verify_reviews(
            state.get("chapter_reviews", []), services["law_store"])}

    def _summarize(state: AgentState) -> dict:
        from agents.contract_review_agent.graph.report import build_report, build_report_json
        meta = {"合同名称": state.get("_file_name", ""),
                "法条库版本": "内置 v1",
                "审核时间": "2026-08-13"}
        reviews = state.get("chapter_reviews", [])
        return {"report": build_report(reviews, meta),
                "report_json": build_report_json(reviews)}

    g = StateGraph(AgentState)
    g.add_node("parse", lambda s: _parse_node(s, services))
    g.add_node("review", _review)
    g.add_node("verify", _verify)
    g.add_node("summarize", _summarize)
    g.add_edge(START, "parse")
    g.add_conditional_edges("parse", _route_after_parse,
                            {"review": "review", "end": END})
    g.add_edge("review", "verify")
    g.add_edge("verify", "summarize")
    g.add_edge("summarize", END)
    return g.compile()
```

- [ ] **Step 4: 实现 agent.py build_graph + run_review**

```python
"""合同审核 agent 图构建入口。

架构见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
from __future__ import annotations

from pathlib import Path

from agents.contract_review_agent.graph.flows import build_graph
from agents.contract_review_agent.store.law_store import LawStore


def _default_law_store() -> LawStore:
    return LawStore(data_dir=Path("data/contract-rag"))


def build_agent() -> LawStore:
    """供 langgraph 注册的入口,返回 LawStore(图构建在 API/CLI 侧用 build_graph)。"""
    return _default_law_store()


def run_review(file_path: str, contract_type: str, review_prompt: str,
               law_store: LawStore | None = None) -> dict:
    """同步跑完整审核流程。返回 {report, report_json, error}。"""
    store = law_store or _default_law_store()
    graph = build_graph(law_store=store)
    state = graph.invoke({
        "_file_path": file_path,
        "_file_name": Path(file_path).name,
        "contract_type": contract_type,
        "review_prompt": review_prompt,
        "chapters": [],
        "chapter_reviews": [],
        "report": "",
        "error": "",
    })
    return {
        "report": state.get("report", ""),
        "report_json": state.get("report_json", {}),
        "error": state.get("error", ""),
    }
```

- [ ] **Step 5: 同步更新 langgraph.json 若 build_graph 名不符**(保持指向 `agent.py:build_graph`,需在 agent.py 暴露 `build_graph = build_graph` 别名或调整注册为 `build_agent`;本计划统一用 `build_graph` 导出):

在 `agent.py` 末尾加:
```python
build_graph = build_graph  # noqa: F811  # langgraph.json 注册入口别名
```
并在 `langgraph.json` 将 contract 注册改为 `./agents/contract_review_agent/agent.py:build_graph`(若 Task 1 已写则不动)。

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/test_contract_review_agent.py -k "build_graph or run_review" -v`
Expected: PASS

- [ ] **Step 7: commit**

```bash
git add agents/contract_review_agent/graph/flows.py agents/contract_review_agent/agent.py tests/test_contract_review_agent.py langgraph.json
git commit -m "feat: 图构建与 run_review 入口(parse→review→verify→summarize)"
```

---

### Task 11: 独立计费/鉴权/配额(common/db.py 加表 + auth.py + billing.py + apikey_mgmt.py)

**Files:**
- Modify: `common/db.py`(`init_tables()` 加 `contract_api_keys` / `contract_billing_records` 两表,项目级改动)
- Create: `agents/contract_review_agent/auth.py`
- Create: `agents/contract_review_agent/billing.py`
- Create: `agents/contract_review_agent/apikey_mgmt.py`
- Modify: `tests/test_contract_review_agent.py`

**Interfaces:**
- Consumes: `common.db.query/execute/transaction/init_tables`
- Produces:
  - `def init_db() -> None` 调 `common.db.init_tables()`(建全表)
  - `def check_apikey(apikey: str) -> dict` 无效/删除 → 401(HTTPException)
  - `def check_quota(apikey: str) -> None` 免费+付费剩余 ≤0 → 403
  - `def create_pending(apikey: str, task_id: str) -> None` 并发 pending 上限 5 → 429
  - `def commit(apikey: str, task_id: str) -> None` 审核完成扣 1 单位(先免费后付费,事务原子)
  - `def cancel_pending(apikey: str, task_id: str) -> None`
  - `def usage(apikey: str) -> dict`
  - `def create_apikey(name: str, role: str = "normal") -> dict` / `def admin_list(apikey: str) -> list[dict]` / `def deactivate_apikey(apikey: str, admin: str) -> None`

- [ ] **Step 1: 改 common/db.py `init_tables()`**,在现有两表后追加(照 §11 init_tables.sql 同步):

```python
        cur.execute(_sql("""
            CREATE TABLE IF NOT EXISTS contract_api_keys (
              apikey      VARCHAR(128) PRIMARY KEY,
              role        VARCHAR(10) NOT NULL DEFAULT 'normal',
              status      VARCHAR(10) NOT NULL DEFAULT 'active',
              free_quota  INT NOT NULL DEFAULT 10,
              paid_quota  INT NOT NULL DEFAULT 0,
              free_used   INT NOT NULL DEFAULT 0,
              paid_used   INT NOT NULL DEFAULT 0,
              created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        cur.execute(_sql("""
            CREATE TABLE IF NOT EXISTS contract_billing_records (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              apikey      VARCHAR(128) NOT NULL,
              task_id     VARCHAR(64) NOT NULL UNIQUE,
              status      VARCHAR(10) NOT NULL DEFAULT 'pending',
              quota_type  VARCHAR(10) NULL,
              created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              committed_at DATETIME NULL
            )
        """))
```
(生产 MySQL 建表走 `deploy/init_tables.sql`,见 Task 14。)

- [ ] **Step 2: 写失败测试**(生产=MySQL,测试用 SQLite 临时库,沿用项目惯例)

```python
import tempfile
from pathlib import Path
from agents.contract_review_agent.billing import (
    init_db, create_pending, commit, cancel_pending, check_quota, usage)
from agents.contract_review_agent.apikey_mgmt import create_apikey

def test_billing_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "test.db"))
    init_db()
    key = create_apikey("tester")["apikey"]
    create_pending(key, "task1")
    commit(key, "task1")
    u = usage(key)
    assert u["free"]["used"] == 1
    assert u["pending_count"] == 0

def test_commit_then_cancel_frees_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "test2.db"))
    init_db()
    key = create_apikey("tester2")["apikey"]
    create_pending(key, "t2")
    cancel_pending(key, "t2")
    assert usage(key)["pending_count"] == 0
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/test_contract_review_agent.py -k billing -v`
Expected: FAIL

- [ ] **Step 4: 实现 auth.py / billing.py / apikey_mgmt.py**(表名 contract_ 前缀,逻辑仿 sentiment 独立实现,SQL 双后端经 `common/db`)

```python
# auth.py
from __future__ import annotations
from fastapi import HTTPException
from common import db

def check_apikey(apikey: str) -> dict:
    rows = db.query("SELECT * FROM contract_api_keys WHERE apikey=%s", (apikey,))
    row = rows[0] if rows else None
    if row is None or row["status"] != "active":
        raise HTTPException(status_code=401, detail="apikey 无效或已删除")
    return row

def require_admin(apikey: str) -> None:
    row = check_apikey(apikey)
    if row["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
```
(apikey_mgmt.py / billing.py 按 spec §5 独立实现,表名 contract_ 前缀,pending 上限 5,commit 事务先免费后付费,一次审核扣 1 单位。)

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_contract_review_agent.py -k billing -v`
Expected: PASS

- [ ] **Step 6: commit**

```bash
git add common/db.py agents/contract_review_agent/auth.py agents/contract_review_agent/billing.py agents/contract_review_agent/apikey_mgmt.py tests/test_contract_review_agent.py
git commit -m "feat: 独立计费/鉴权(contract_api_keys/billing_records, 按次扣费)"
```

> 注:`common/db.py` 改动记根 `CHANGELOG.md` 项目级区。

---

### Task 12: API(api.py)

**Files:**
- Modify: `agents/contract_review_agent/api.py`(实现 FastAPI)
- Modify: `tests/test_contract_review_agent.py`

**Interfaces:**
- Consumes: `run_review`(Task 10)、`optimize_review_prompt`(Task 9)、billing/auth( Task 11)、`LawStore`(Task 3)
- Produces:`app = FastAPI()` 与接口:
  - `POST /api/v1/contract/review` multipart(file, contract_type, prompt, apikey header)→ 校验 apikey/额度/大小 → 建 pending(task_id)→ 后台线程跑 `run_review` → SSE 回显进度 → 完成 `commit` → 存报告
  - `GET /api/v1/contract/status?task_id=` → 状态+进度
  - `GET /api/v1/contract/result?task_id=` → JSON + report
  - `POST /api/v1/contract/stop` → `cancel_pending`
  - `POST /api/v1/contract/prompt` → F1
  - `POST /api/v1/laws/upload` → 法条入库
  - `GET /api/v1/laws` → 法条列表
  - `POST /api/v1/apikeys` / `GET /api/v1/apikeys` / `DELETE /api/v1/apikeys` → 独立 apikey 管理
  - `GET /health`

- [ ] **Step 1: 写失败测试**(TestClient 冒烟:健康检查 + 未鉴权接口 401)

```python
from fastapi.testclient import TestClient
from agents.contract_review_agent.api import app

def test_health():
    c = TestClient(app)
    assert c.get("/health").status_code == 200

def test_review_requires_apikey():
    c = TestClient(app)
    files = {"file": ("x.docx", b"not a real docx", "application/octet-stream")}
    r = c.post("/api/v1/contract/review", files=files, data={"contract_type": "劳动合同", "prompt": "审"})
    assert r.status_code in (401, 422)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_contract_review_agent.py -k "health or requires_apikey" -v`
Expected: FAIL

- [ ] **Step 3: 实现 api.py**(FastAPI + sse-starlette,仿 sentiment api.py 结构;文件存临时目录,大小/类型校验走 Task 4 异常;SSE 按章节进度事件)

```python
"""合同审核 agent FastAPI 接口。架构见 design doc §7。"""
from __future__ import annotations

import tempfile
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, UploadFile, HTTPException
from sse_starlette.sse import EventSourceResponse

from agents.contract_review_agent import billing, auth
from agents.contract_review_agent.store.law_store import LawStore

app = FastAPI(title="contract_review_agent")
_law_store = LawStore(data_dir=Path("data/contract-rag"))
_tasks: dict[str, dict] = {}  # task_id -> {status, progress, result, error}
_lock = threading.Lock()


@app.get("/health")
def health():
    return {"status": "ok"}


def _require_key(apikey: str) -> dict:
    return auth.check_apikey(apikey)


@app.post("/api/v1/contract/prompt")
def contract_prompt(contract_type: str = Form(...), prompt: str = Form(...),
                    apikey: str = Header(...)):
    _require_key(apikey)
    from agents.contract_review_agent.graph.prompt_node import optimize_review_prompt
    return {"prompt": optimize_review_prompt(contract_type, prompt)}


@app.post("/api/v1/laws/upload")
def laws_upload(apikey: str = Header(...), file: UploadFile = File(...)):
    auth.require_admin(apikey)
    content = file.file.read().decode("utf-8", errors="replace")
    return _law_store.seed(content)


@app.get("/api/v1/laws")
def laws_list(apikey: str = Header(...)):
    _require_key(apikey)
    return {"laws": _law_store.list_laws()}


def _run_task(task_id: str, file_path: str, contract_type: str,
              prompt: str, apikey: str) -> None:
    from agents.contract_review_agent.agent import run_review
    result = run_review(file_path, contract_type, prompt, law_store=_law_store)
    with _lock:
        t = _tasks[task_id]
        t["status"] = "done" if not result["error"] else "failed"
        t["error"] = result["error"]
        t["result"] = result
        t["progress"] = 1.0
    if not result["error"]:
        billing.commit(apikey, task_id)
    else:
        billing.cancel_pending(apikey, task_id)
    Path(file_path).unlink(missing_ok=True)


@app.post("/api/v1/contract/review")
async def review(apikey: str = Header(...), contract_type: str = Form(...),
                 prompt: str = Form(...), file: UploadFile = File(...)):
    key = _require_key(apikey)
    billing.check_quota(apikey)
    suffix = Path(file.filename or "x.docx").suffix.lower()
    if suffix not in (".docx", ".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 docx/pdf")
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    with open(fd, "wb") as f:
        f.write(await file.read())
    task_id = uuid.uuid4().hex
    billing.create_pending(apikey, task_id)
    with _lock:
        _tasks[task_id] = {"status": "running", "progress": 0.0,
                           "result": None, "error": "", "apikey": apikey}
    threading.Thread(target=_run_task,
                     args=(task_id, tmp, contract_type, prompt, apikey),
                     daemon=True).start()

    def gen():
        yield {"event": "started", "data": task_id}
        while True:
            with _lock:
                t = _tasks.get(task_id)
            if t is None:
                break
            yield {"event": "progress", "data": str(t["progress"])}
            if t["status"] in ("done", "failed"):
                yield {"event": t["status"], "data": str(t["error"] or "")}
                break
            import time
            time.sleep(0.5)

    return EventSourceResponse(gen())


@app.get("/api/v1/contract/status")
def status(task_id: str, apikey: str = Header(...)):
    _require_key(apikey)
    with _lock:
        t = _tasks.get(task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"task_id": task_id, "status": t["status"], "progress": t["progress"]}


@app.get("/api/v1/contract/result")
def result(task_id: str, apikey: str = Header(...)):
    _require_key(apikey)
    with _lock:
        t = _tasks.get(task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if t["status"] not in ("done", "failed"):
        raise HTTPException(status_code=409, detail="任务未完成")
    return {"task_id": task_id, "status": t["status"],
            "result": t["result"] or {"error": t["error"]}}


@app.post("/api/v1/contract/stop")
def stop(task_id: str, apikey: str = Header(...)):
    _require_key(apikey)
    with _lock:
        t = _tasks.get(task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    t["status"] = "cancelled"
    billing.cancel_pending(apikey, task_id)
    return {"ok": True}


@app.post("/api/v1/apikeys")
def api_create(name: str = Form(...), admin: str = Header(...)):
    from agents.contract_review_agent.apikey_mgmt import create_apikey
    auth.require_admin(admin)
    return create_apikey(name)


@app.get("/api/v1/apikeys")
def api_list(admin: str = Header(...)):
    from agents.contract_review_agent.apikey_mgmt import admin_list
    auth.require_admin(admin)
    return {"apikeys": admin_list()}


@app.delete("/api/v1/apikeys/{apikey}")
def api_delete(apikey: str, admin: str = Header(...)):
    from agents.contract_review_agent.apikey_mgmt import deactivate_apikey
    auth.require_admin(admin)
    deactivate_apikey(apikey)
    return {"ok": True}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_contract_review_agent.py -k "health or requires_apikey" -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add agents/contract_review_agent/api.py tests/test_contract_review_agent.py
git commit -m "feat: FastAPI 接口(审核/状态/结果/F1/法条库/apikey)"
```

---

### Task 13: 种子数据采集(人工)+ 端到端验证

**Files:**
- Create: `agents/contract_review_agent/data/laws/labor_law.md`、`labor_contract_law.md`、`civil_code_contract.md`(人工填充)
- Create: `tests/fixtures/contract_sample.docx`(样例劳动合同,人工构造)

- [ ] **Step 1: 人工从 flk.npc.gov.cn 采集法条原文**,填三个 md(劳动法 107 条、劳动合同法 98 条、合同编高频 ~100 条),格式对齐 Task 2(标题 `# 名称`、`来源:`、`采集日期:`、`领域:`,每条 `## 第X条`)。**严禁 AI 编造条文**。
- [ ] **Step 2: 灌库**

Run: `python -m agents.contract_review_agent.scripts.seed_laws`
Expected: 三法条数打印,errors 为空

- [ ] **Step 3: 构造样例劳动合同 docx**(含常见问题条款:过高违约金、无试用期约定、单方解除权等)
- [ ] **Step 4: 端到端审核**

Run: `python -c "from agents.contract_review_agent.agent import run_review; print(run_review('tests/fixtures/contract_sample.docx', '劳动合同', '请审核违约金、试用期、解除条款'))"`
Expected: 报告含 findings,statutory 结论全部可核验(校验层保证)

- [ ] **Step 5: commit**(法条数据为人工权威原文,commit 记录来源)

```bash
git add agents/contract_review_agent/data/laws/ tests/fixtures/contract_sample.docx
git commit -m "data: 内置法条种子(劳动法/劳动合同法/合同编高频,权威来源采集)"
```

---

### Task 14: 文档收尾 + 版本 + 收尾约定

**Files:**
- Create: `agents/contract_review_agent/API.md`(接口文档,仿 sentiment,含真实返回示例)
- Modify: `agents/contract_review_agent/CHANGELOG.md`(补各任务变更,bump 到 v0.1.x)
- Modify: `agents/contract_review_agent/deploy/`(Dockerfile/compose/deploy.sh/init_tables.sql,若部署纳入本批;否则单列部署任务)

- [ ] **Step 1: 写 API.md**(全接口 + 请求/响应示例)
- [ ] **Step 2: 更新 CHANGELOG.md**(逐任务记录,bump 版本号当前最大 +1)
- [ ] **Step 3: 跑全量测试**

Run: `pytest tests/test_contract_review_agent.py -v`
Expected: ALL PASS

- [ ] **Step 4: commit**

```bash
git add agents/contract_review_agent/API.md agents/contract_review_agent/CHANGELOG.md
git commit -m "docs: 合同审核 agent API.md + CHANGELOG bump"
```

---

## 自审记录

- **Spec 覆盖**:F1(Task 9)、F2 章节流水线(Task 4/6/7/8/10)、法条库双存储+领域过滤(Task 2/3)、OCR 百度云端(Task 5)、大小限制(§4.1 Task 4)、独立计费(Task 11)、API+配额+部署(Task 12,部署套件 Task 14 提及)、测试策略(Task 2-12 内嵌)。报告模板 §6 由 Task 8 落地。
- **无占位**:各任务含实际代码与测试;Task 12 部分接口标"实现时补齐"——在 Task 12 Step 3 内列出全部接口签名与处理路径,后续细化。
- **类型一致**:`LawStore.retrieve/verify_ref`、`ChapterReview/Finding/LegalRef`、`run_review` 返回结构在 Task 3/6/7/10 间一致。
