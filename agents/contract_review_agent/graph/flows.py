"""LangGraph 图:parse → review → verify → summarize。

图结构(设计 §3,与 sentiment 同风格):
  START → parse → review → verify → summarize → END
  parse:      文件解析层(docx / pdf 文本层 / 无文本层 OCR 标记)→ Document{chapters[]}
  review:     逐章检索法条 → LLM(temperature=0.1)审核 → chapter_reviews
  verify:     引用校验层(核心反幻觉):条号存在 + 引文 fuzzy match,失败降级
  summarize:  合并 findings → 风险排序 → JSON + markdown 报告(声明法条库版本)

parse 失败条件路由:needs_ocr / too_long / unsupported → END(错误写入 state["error"],
不中断图,与 sentiment"单步失败标 error 不中断"约定一致)。

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

from agents.contract_review_agent.graph.state import AgentState


def _parse_node(state: AgentState, services: dict) -> dict:
    """解析节点:统一捕获解析层异常,映射为明确 error 码(供条件路由到 END)。"""
    from agents.contract_review_agent.utils.document_parser import (
        ContractTooLongError,
        NeedsOcrError,
        UnsupportedTypeError,
        parse_document,
    )
    try:
        doc = parse_document(state["_file_path"])
    except NeedsOcrError:
        return {"error": "needs_ocr"}
    except ContractTooLongError:
        return {"error": "too_long"}
    except (UnsupportedTypeError, FileNotFoundError):
        return {"error": "unsupported"}
    except Exception:
        # 损坏/伪造文件等解析失败(python-docx/pypdf 非四种声明异常):
        # 归为不支持类型,条件路由到 END,不让未知异常中断整图。
        return {"error": "unsupported"}
    return {"chapters": [c.model_dump() for c in doc.chapters]}


def _route_after_parse(state: AgentState) -> Literal["review", "end"]:
    return "end" if state.get("error") else "review"


def build_graph(law_store=None) -> Runnable:
    """返回编译后的 LangGraph 图。law_store 为 None 时审核不注入法条片段(纯 mock 路径)。

    Args:
        law_store: LawStore 实例。None 时 review/verify 跳过法条检索与核验
            (供纯 mock/无法条库环境);生产路径由 run_review 传入默认
            data/contract-rag 法条库。
    """
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
        from agents.contract_review_agent.graph.report import (
            build_report,
            build_report_json,
        )
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
