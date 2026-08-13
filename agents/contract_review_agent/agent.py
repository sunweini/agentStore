"""合同审核 agent 图构建入口:build_graph + run_review。

- build_graph:供 langgraph.json 注册的入口(`agent.py:build_graph`),
  返回编译后的 LangGraph 图(parse → review → verify → summarize)。
- run_review:同步一次跑完整图(供 API/CLI 调用),返回 {report, report_json, error};
  法条库缺省用 data/contract-rag(LawStore 懒构造,首用才建 Chroma)。

架构见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
from __future__ import annotations

from pathlib import Path

from agents.contract_review_agent.graph.flows import build_graph
from agents.contract_review_agent.store.law_store import LawStore


def _default_law_store() -> LawStore:
    return LawStore(
        data_dir=Path("data/contract-rag"),
        laws_dir=Path("agents/contract_review_agent/data/laws"))


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


build_graph = build_graph  # noqa: F811  # langgraph.json 注册入口别名
