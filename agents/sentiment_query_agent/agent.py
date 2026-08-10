"""sentiment-query-agent 图构建入口:6 步流水线 + AsyncSqliteSaver checkpointer。

设计见 docs/superpowers/specs/2026-08-06-sentiment-query-agent-sentiment-query-agent-design.md。
langgraph.json 注册入口: "./agents/sentiment_query_agent/agent.py:build_agent"
"""

from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agents.sentiment_query_agent.graph.flows import build_graph

_CHECKPOINT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "checkpoints.sqlite"


def build_agent():
    """构建 sentiment-query-agent 图(未挂 checkpointer 的裸图)。

    langgraph.json 注册入口。实际运行时(checkpointer)由 run_pipeline 管理。

    Returns:
        编译后的 StateGraph(无持久化,供 langgraph CLI/部署使用)。
    """
    return build_graph().compile()


async def run_pipeline(group_id: str, company_name: str, owner: str, meta: dict) -> dict:
    """完整跑一次 6 步流水线(后台任务入口,带持久化 checkpointer)。

    Args:
        group_id: 方案组 ID(= thread_id)。
        company_name: 中文公司名。
        owner: 用户(apikey 标识)。
        meta: 主体角色/地区/检索类型。

    Returns:
        最终状态 dict(含 schemes)。

    注:AsyncSqliteSaver.from_conn_string 返回异步上下文管理器,
    async with 进入后才拿到 saver 实例(官方 API 行为)。
    """
    _CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(_CHECKPOINT_DB)) as saver:
        # WAL:流水线写与进度轮询读走不同连接,防并发 database is locked
        await saver.conn.execute("PRAGMA journal_mode=WAL")
        graph = build_graph().compile(checkpointer=saver)
        from datetime import datetime

        from agents.sentiment_query_agent.graph.state import STATUS_GENERATING, STATUS_REVIEW

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
