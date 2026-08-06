"""websearch 池:gateway MCP 封装(brave/tavily/serpapi 三引擎)。

设计见 docs/superpowers/specs/2026-08-06-agent1-sentiment-query-agent-design.md §2/§10。

- 应用启动时建单例连接(MultiServerMCPClient),get_tools() 结果缓存复用,
  不在每次跑图时重建。
- 失败自动切换引擎重试(3 引擎池)。
- 配置:.env 的 MCP_GATEWAY_URL / MCP_GATEWAY_TOKEN(默认 10.33.17.72:8082)。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_ENGINES = ["brave", "tavily", "serpapi"]
_tools: dict[str, Any] | None = None
_client: Any | None = None


async def _get_tools() -> dict[str, Any]:
    """获取 MCP 工具(单例,缓存复用)。失败抛 RuntimeError。"""
    global _tools, _client
    if _tools is not None:
        return _tools
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from common import config

        url = config.get_env("MCP_GATEWAY_URL", "http://10.33.17.72:8082/mcp")
        token = config.get_env("MCP_GATEWAY_TOKEN")
        _client = MultiServerMCPClient({
            "gateway": {
                "transport": "http",
                "url": url,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        })
        tools = await _client.get_tools()
        # 工具名 → 工具映射,供按引擎调用
        _tools = {getattr(t, "name", ""): t for t in tools}
        logger.info("service=agent1 event=mcp_connected tools=%d", len(_tools))
        return _tools
    except Exception as exc:
        raise RuntimeError(f"MCP gateway 连接失败: {exc}") from exc


async def websearch(query: str, engine: str = "auto", **kwargs) -> str:
    """websearch 池统一入口。

    Args:
        query: 搜索词。
        engine: auto(按查询类型选)/ brave / tavily / serpapi。
        kwargs: 透传引擎参数(如 max_results)。

    Returns:
        搜索结果字符串(合并多引擎结果,供 LLM 读取)。

    Raises:
        RuntimeError: 全部引擎失败。
    """
    tools = await _get_tools()
    engines = _ENGINES if engine == "auto" else [engine]

    results = []
    last_err: Exception | None = None
    for eng in engines:
        # 按引擎找工具:brave_web_search / tavily_search / serpapi_google
        candidates = [n for n in tools if n.startswith(eng)]
        if not candidates:
            logger.warning("service=agent1 event=engine_missing engine=%s", eng)
            continue
        tool = tools[candidates[0]]
        try:
            logger.info("service=agent1 event=search_start engine=%s query_len=%d", eng, len(query))
            resp = await tool.ainvoke({**kwargs, "query": query})
            results.append(f"[{eng}] {resp}")
            break  # 首个成功引擎即返回
        except Exception as exc:
            last_err = exc
            logger.warning("service=agent1 event=engine_fail engine=%s error=%s", eng, exc)
            continue

    if not results:
        raise RuntimeError(f"websearch 全部引擎失败: {last_err}")
    return "\n".join(results)
