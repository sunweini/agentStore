"""节点函数:agent1 的图节点与路由。

设计见 docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md 第 6 节。

节点职责:
- agent_node:加载 prompt,把消息历史发 LLM,返回 LLM 输出。
- should_continue:路由 —— 最后一条消息有 tool_calls 走 tools_node,否则 END。

错误处理(设计文档第 8 节):
- LLM 调用失败:异常转错误消息回图,不中断整个流程。
- 工具执行失败:由 ToolNode 兜底(异常转 ToolMessage 回 LLM)。
- 死循环:图配置 recursion_limit,超限抛错终止。

工具注入:agent_node 直接绑定本 agent 的 TOOLS 常量(agents/agent1/utils/tools.py)。
增删工具只改 TOOLS 列表,图结构/节点不动。
"""

from __future__ import annotations

import logging
import time
import uuid

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END

from agents.agent1.utils.state import AgentState
from agents.agent1.utils.tools import TOOLS
from common import prompts
from common.llm import get_chat_model

logger = logging.getLogger(__name__)

_AGENT = "agent1"


def _log_span(event: str, span_id: str, **fields: object) -> None:
    """结构化日志(遵循可观测性规范):service/span_id/route + key=value。"""
    logger.info(
        "service=agent1 route=agent_node span_id=%s event=%s %s",
        span_id,
        event,
        " ".join(f"{k}={v}" for k, v in fields.items()),
    )


def agent_node(state: AgentState) -> AgentState:
    """主节点:LLM 决策。加载 prompt,发消息历史,返回 LLM 输出。

    无 tool_calls → should_continue 路由到 END;
    有 tool_calls → 路由到 tools_node 执行,结果回传后再次进入本节点。
    """
    span_id = uuid.uuid4().hex[:8]
    _log_span("node_start", span_id, task_len=len(state.get("task", "")))
    start = time.monotonic()

    try:
        prompt = prompts.load_prompt(_AGENT)  # agents/agent1/prompts/system.md
        llm = get_chat_model().bind_tools(TOOLS)
        messages = state.get("messages", [])
        response = llm.invoke([*prompt.format_messages(), *messages])
        _log_span(
            "node_end",
            span_id,
            tool_calls=len(getattr(response, "tool_calls", [])),
            duration_ms=round((time.monotonic() - start) * 1000),
        )
        return {"messages": [response]}
    except Exception as exc:  # LLM/工具调用异常不中断整个图
        _log_span("node_error", span_id, error=str(exc))
        err_msg: BaseMessage = AIMessage(
            content=f"处理任务时出错,请稍后重试或换一种问法。错误: {exc}",
            name=f"error_{_AGENT}",
        )
        return {"messages": [err_msg], "result": f"ERROR: {exc}"}


def should_continue(state: AgentState) -> str:
    """路由:最后一条消息有 tool_calls → 走 tools_node;否则 END。

    官方标准路由模式。
    """
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END
