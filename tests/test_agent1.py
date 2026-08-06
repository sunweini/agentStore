"""agent1 测试:三层 —— 工具单测 / 图单测(mock LLM)/ 端到端(需 key)。

设计见 docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md 第 10 节。

端到端测试(test_e2e)依赖真实 DEEPSEEK_API_KEY,
无 key 时自动跳过(不会报错),有 key 时跑通完整链路。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from agents.agent1.agent import build_agent
from agents.agent1.utils.state import AgentState
from agents.agent1.utils.tools import check_stock, search_material
from common import config


# ===== 1. 工具单测:直接调工具函数 =====


def test_search_material_returns_json():
    result = search_material.invoke({"material_code": "MAT-001"})
    assert '"MAT-001"' in result
    assert '"stock"' in result


def test_check_stock_returns_json():
    result = check_stock.invoke({"warehouse": "成品仓"})
    assert '"成品仓"' in result


# ===== 2. 图单测:mock LLM,断言走节点/路由 =====


class _FakeLLM:
    """假 LLM:按调用次数依次返回预设 AIMessage(可含 tool_calls)。"""

    def __init__(self, responses: list[AIMessage]):
        self.responses = list(responses)
        self.calls = 0

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages, **kwargs):
        self.calls += 1
        return self.responses[min(self.calls, len(self.responses)) - 1]


@tool
def _mock_search(code: str) -> str:
    """查库存。"""
    return f"库存 {code}: 100 件"


def _make_graph_with_llm(responses: list[AIMessage]):
    """构建替换了 LLM 和工具的 agent1 图(不碰真实 LLM/API)。

    注意:get_chat_model 在 graph.invoke 时才调用,所以 patch 必须
    覆盖整个运行期 —— _run 里再做 patch,这里只返回图。
    """
    fake = _FakeLLM(responses)
    with patch("agents.agent1.utils.nodes.TOOLS", [_mock_search]):
        graph = build_agent()
    return graph, fake


def _run(graph, task: str, fake: _FakeLLM) -> dict:
    with patch("agents.agent1.utils.nodes.get_chat_model", return_value=fake):
        return graph.invoke(
            AgentState(messages=[HumanMessage(content=task)], task=task),
            config={"recursion_limit": 10},  # 官方:递归上限是运行时 config 参数
        )


def test_graph_ends_without_tool_calls():
    """LLM 直接回答,无 tool_calls → 单节点走到 END。"""
    graph, fake = _make_graph_with_llm([AIMessage(content="最终回答")])
    result = _run(graph, "简单问题", fake)
    assert fake.calls == 1
    assert result["messages"][-1].content == "最终回答"


def test_graph_tool_loop():
    """LLM 先请求工具,拿到结果后再回答 → 走完 agent→tools→agent→END。"""
    # 第 1 次:请求 _mock_search 工具;第 2 次:直接回答。
    tool_call_msg = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "_mock_search",
                "args": {"code": "MAT-001"},
                "id": "call_1",
                "type": "tool_call",
            }
        ],
    )
    graph, fake = _make_graph_with_llm([tool_call_msg, AIMessage(content="查完了")])
    result = _run(graph, "查一下 MAT-001 库存", fake)
    assert fake.calls == 2
    # 中间应有 ToolMessage(工具结果)
    assert any(m.type == "tool" for m in result["messages"])


# ===== 3. 端到端:真实 DeepSeek,无 key 自动跳过 =====


def _has_api_key() -> bool:
    return bool(config.provider_api_key("deepseek"))


@pytest.mark.skipif(
    not _has_api_key(),
    reason="DEEPSEEK_API_KEY 未配置,跳过端到端测试",
)
def test_e2e_full_flow():
    """真实调用:任务要求查库存,LLM 应调用工具并给出最终回答。"""
    graph = build_agent()
    task = "查一下物料 MAT-001 的库存,并告诉我数量"
    result = graph.invoke(
        AgentState(messages=[HumanMessage(content=task)], task=task),
        config={"recursion_limit": 25},
    )
    final = result["messages"][-1].content
    assert isinstance(final, str) and final
