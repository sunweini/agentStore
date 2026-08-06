"""agent1 图构建入口:6 步流水线 + AsyncSqliteSaver checkpointer。

设计见 docs/superpowers/specs/2026-08-06-agent1-sentiment-query-agent-design.md。
langgraph.json 注册入口: "./agents/agent1/agent.py:build_agent"
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agents.agent1.graph.flows import build_graph

_CHECKPOINT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "checkpoints.sqlite"


async def build_agent():
    """构建并编译 agent1 图,挂 AsyncSqliteSaver(本地持久化)。

    Returns:
        编译后的 StateGraph + checkpointer。使用:
        graph = await build_agent()
        await graph.ainvoke(inputs, config={"configurable": {"thread_id": group_id}})

    注:AsyncSqliteSaver 需要事件循环内创建,故 build_agent 为 async。
    """
    graph = build_graph()
    _CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    # async with 生命周期:由调用方(api.py)持有 saver 上下文。
    # 这里返回编译图 + saver,调用方管理连接。
    saver = AsyncSqliteSaver.from_conn_string(str(_CHECKPOINT_DB))
    compiled = graph.compile(checkpointer=saver)
    return compiled, saver


async def run_pipeline(group_id: str, company_name: str, owner: str, meta: dict) -> dict:
    """完整跑一次 6 步流水线(后台任务入口)。

    Args:
        group_id: 方案组 ID(= thread_id)。
        company_name: 中文公司名。
        owner: 用户(apikey 标识)。
        meta: 主体角色/地区/检索类型。

    Returns:
        最终状态 dict(含 schemes)。
    """
    graph, saver = await build_agent()
    try:
        from agents.agent1.graph.state import STATUS_GENERATING, STATUS_REVIEW
        from datetime import datetime

        initial = {
            "messages": [],
            "current_step": 0,
            "group": {
                "group_id": group_id,
                "owner": owner,
                "company_name": company_name,
                "meta": meta,
                "status": STATUS_GENERATING,
                "step_status": [],
                "schemes": [],
                "created_at": datetime.now().isoformat(),
                "committed_at": None,
            },
        }
        result = await graph.ainvoke(
            initial,
            config={"configurable": {"thread_id": group_id}},
        )
        result["group"]["status"] = STATUS_REVIEW  # 6 步完成 → 待勾选
        return result["group"]
    finally:
        await saver.close()
