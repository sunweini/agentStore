"""LangGraph 图:parse → review → verify → summarize。

图结构(设计 §3,与 sentiment 同风格):
  START → parse → review → verify → summarize → END
  parse:      文件解析层(docx / pdf 文本层 / 无文本层走百度云端 OCR)→ Document{chapters[]}
  review:     逐章检索法条 → LLM(temperature=0.1)审核 → chapter_reviews
  verify:     引用校验层(核心反幻觉):条号存在 + 引文 fuzzy match,失败降级
  summarize:  合并 findings → 风险排序 → JSON + markdown 报告(声明法条库版本)

parse 失败条件路由:too_long / unsupported / ocr_unconfigured / ocr_failed → END
(错误写入 state["error"],不中断图,与 sentiment"单步失败标 error 不中断"约定一致)。

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from langgraph.graph import END, START, StateGraph

from agents.contract_review_agent.graph.state import AgentState

logger = logging.getLogger(__name__)
_SERVICE = "contract_review_agent"


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
        return _ocr_parse(state)
    except ContractTooLongError:
        return {"error": "too_long"}
    except (UnsupportedTypeError, FileNotFoundError):
        return {"error": "unsupported"}
    except Exception as exc:
        # 损坏/伪造文件等解析失败(python-docx/pypdf 非四种声明异常):
        # 归为不支持类型,条件路由到 END,不让未知异常中断整图。
        # 结构化日志(OBS-CORE-001)只记 error_type 不记 str(exc):解析异常消息
        # 可能携带文件内容/路径等敏感信息(与 api.py 防凭据泄露约定一致)。
        logger.error("service=%s event=parse_unexpected_error file_name=%s error_type=%s",
                     _SERVICE, state.get("_file_name", ""), type(exc).__name__)
        return {"error": "unsupported"}
    return {"chapters": [c.model_dump() for c in doc.chapters]}


def _ocr_parse(state: AgentState) -> dict:
    """NeedsOcrError 分支:扫描件 pdf 走百度云端 OCR 提取文本后照常分章。

    缺凭据(BAIDU_OCR_* 未配)→ ocr_unconfigured(不调 OCR);取 token 失败 /
    OCR 调用异常 / 返回空文本 → ocr_failed。结构化日志只记 error_type 不记
    str(exc):异常详情可能含文件内容/接口返回等敏感信息。OCR 文本质量调优
    (识别精度/分章)为后续版本,此处复用 _looks_like_heading 启发式分章即可。
    """
    from agents.contract_review_agent.utils import ocr_client
    from agents.contract_review_agent.utils.chapterizer import build_chapters
    from agents.contract_review_agent.utils.document_parser import _looks_like_heading

    try:
        token = ocr_client.get_token()
    except Exception as exc:
        logger.error("service=%s event=ocr_token_failed file_name=%s error_type=%s",
                     _SERVICE, state.get("_file_name", ""), type(exc).__name__)
        return {"error": "ocr_failed"}
    if not token:
        return {"error": "ocr_unconfigured"}
    try:
        text = ocr_client.ocr_pdf_pages(state["_file_path"], token)
    except Exception as exc:
        logger.error("service=%s event=ocr_failed file_name=%s error_type=%s",
                     _SERVICE, state.get("_file_name", ""), type(exc).__name__)
        return {"error": "ocr_failed"}
    if not text or not text.strip():
        logger.warning("service=%s event=ocr_failed file_name=%s error_type=empty",
                       _SERVICE, state.get("_file_name", ""))
        return {"error": "ocr_failed"}
    blocks: list[tuple[str, int]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        blocks.append((line, 1 if _looks_like_heading(line) else 0))
    chapters = build_chapters(blocks)
    return {"chapters": [c.model_dump() for c in chapters]}


def _route_after_parse(state: AgentState) -> Literal["review", "end"]:
    return "end" if state.get("error") else "review"


def build_graph(law_store=None) -> Runnable:
    """返回编译后的 LangGraph 图。law_store 缺省用内置法条库,校验层恒开启。

    Args:
        law_store: LawStore 实例。None 时使用默认 data/contract-rag + 内置
            data/laws 法条库(等价 agent._default_law_store),保证任何入口
            (含 langgraph.json 无参注册调用 `agent.py:build_graph`)构造的图
            都不关闭引用校验层 —— 否则 langgraph server 跑图会静默跳过核验,
            反幻觉铁律失效(终审 finding #6)。
    """
    if law_store is None:
        # 函数体内 import,避免 agent → flows 模块级循环 import
        # (agent.py 顶层已 from flows import build_graph)。
        from agents.contract_review_agent.agent import _default_law_store
        law_store = _default_law_store()
    services = {"law_store": law_store}

    def _review(state: AgentState) -> dict:
        from agents.contract_review_agent.graph.nodes import review_all
        return review_all(state, services["law_store"])

    def _verify(state: AgentState) -> dict:
        from agents.contract_review_agent.graph.verify import verify_reviews
        # law_store 由 build_graph 兜底为非 None(默认法条库):不再存在
        # "无 law_store 透传不核验"路径,任何构造路径校验层均开启。
        return {"chapter_reviews": verify_reviews(
            state.get("chapter_reviews", []), services["law_store"])}

    def _summarize(state: AgentState) -> dict:
        from agents.contract_review_agent.graph.report import (
            build_report,
            build_report_json,
        )
        meta = {"合同名称": state.get("_file_name", ""),
                "法条库版本": "内置 v1",
                "审核时间": datetime.now().strftime("%Y-%m-%d %H:%M")}
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
