"""工具定义:agent1 专属工具。

设计见 docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md 第 7 节。

第一版:占位工具,验证「LLM → 工具调用 → 回传 → 再决策」闭环,不接真实业务。
后续替换真实 API 只改工具函数内部,签名/图结构不动。

注意:工具返回值用字符串,不要用结构化对象 —— LangChain 工具约定
(LLM 读字符串最稳,结构化对象易出序列化问题)。
"""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def search_material(material_code: str) -> str:
    """按物料编码查库存。返回该物料的库存 JSON。

    Args:
        material_code: 物料编码,如 "MAT-001"。
    """
    # 占位实现:mock 数据,验证工具调用链路。
    # 后续替换为真实数据源(如金蝶库存查询 API)。
    mock = {
        "material_code": material_code,
        "name": f"物料 {material_code}",
        "stock": 120,
        "unit": "件",
        "warehouse": "成品仓",
        "source": "mock(占位,待接真实数据源)",
    }
    import json

    return json.dumps(mock, ensure_ascii=False)


@tool
def check_stock(warehouse: str, material_code: str = "") -> str:
    """查指定仓库实时库存。返回仓库库存 JSON。

    Args:
        warehouse: 仓库名,如 "成品仓"。
        material_code: 物料编码(可选)。缺省返回该仓库全部物料库存。
    """
    # 占位实现:mock 数据,验证工具调用链路。
    mock = {
        "warehouse": warehouse,
        "total_items": 8,
        "stock": {material_code or "全部物料": 120},
        "source": "mock(占位,待接真实数据源)",
    }
    import json

    return json.dumps(mock, ensure_ascii=False)


# 本 agent 可用工具列表,构建图时注入。
TOOLS = [search_material, check_stock]
