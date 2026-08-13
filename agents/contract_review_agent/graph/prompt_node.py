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
                    "补充类型常见风险与引用法规指引。只输出优化后的 prompt 本身,不要解释。"
                    "输出的 prompt 必须包含一条明确的引用指引:法律依据只允许引用法条库"
                    "片段原文,禁止编造;没有可依据的条款时标注'仅提示,非强制'。"},
        {"role": "user", "content": f"合同类型:{contract_type}\n原始要求:{user_prompt}"},
    ])
    out = resp.content if isinstance(resp.content, str) else str(resp.content)
    if not out.strip():
        out = filled
    return out
