"""load_skill 工具:skill 渐进式披露加载(对照 sentiment-query-agent 模式)。

设计对照:agents/sentiment_query_agent/skills/loader.py(sentiment 6 步流水线)。

- agent 启动只加载 skill 摘要(_AVAILABLE_SKILLS,注入系统提示)。
- worker 需要时调 load_skill 取完整内容(SKILL.md + 类型模板)。
- 查找顺序:agents/kingdee_plugin_agent/skills/ → common/skills/。
- 与 sentiment 版差异:requirement-clarify 无 references/ 子目录,
  类型模板(bill.md/service.md/list.md)直接放在 skill 目录,load_skill
  返回的 references 字段即这些模板文件;scripts 恒为空列表(无分步脚本)。
- structured_with_skill:结构化输出 + load_skill 绑定(官方 tools 参数,
  langchain-openai 1.4.1 源码实测核对 —— bind_tools 后再 with_structured_output
  会经 __getattr__ 委派丢失 tools,必须用 with_structured_output(schema,
  tools=[load_skill], include_raw=True) 形态),脚本/fake LLM(无 bind_tools)
  自动跳过绑定,既有测试契约不变。
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_AGENT_SKILLS = _PROJECT_ROOT / "agents" / "kingdee_plugin_agent" / "skills"
_COMMON_SKILLS = _PROJECT_ROOT / "common" / "skills"

# 本 agent 可用的 skill 摘要(启动即加载,渐进式披露的"摘要层")
_AVAILABLE_SKILLS = {
    "requirement-clarify": (
        "金蝶插件需求澄清方法论:单据/服务/列表三套元数据驱动问题模板"
        "(单据-触发操作/校验字段/拦截方式/联动单据/异常处理;"
        "服务-入口/事务边界/异常回滚;列表-字段/按钮/过滤),"
        "一次一问、多选优先、10 轮上限,spec 决策+假设清单"
    ),
}

#: 每步注入的 load_skill 提示(告诉 LLM 可调工具拿方法论,对照 sentiment 方案 2a)
SKILL_HINT = (
    "\n\n可用工具: load_skill(skill_name)。金蝶插件方法论(需求澄清问题模板、"
    "设计/生成/审查要点)需要时调用 load_skill 获取专业指导,"
    "工具返回内容仅供参考,不改变输出格式。"
)


def _find_skill(name: str) -> Path | None:
    """按 agent → common 顺序查找 skill 目录。"""
    for root in (_AGENT_SKILLS, _COMMON_SKILLS):
        p = root / name
        if p.is_dir():
            return p
    return None


@tool
def load_skill(skill_name: str) -> str:
    """按需加载 skill 完整内容(SKILL.md + 类型模板文件)。

    Args:
        skill_name: skill 名(requirement-clarify)。
    """
    if skill_name not in _AVAILABLE_SKILLS:
        return json.dumps(
            {"error": f"skill {skill_name} 不可用", "available": sorted(_AVAILABLE_SKILLS)},
            ensure_ascii=False,
        )
    skill_dir = _find_skill(skill_name)
    if skill_dir is None:
        return json.dumps({"error": f"skill {skill_name} 目录缺失"}, ensure_ascii=False)

    skill_md = skill_dir / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8") if skill_md.exists() else "(SKILL.md 缺失)"
    # requirement-clarify 无 references/ 子目录:类型模板 .md 直接放 skill 目录
    refs = sorted(p.name for p in skill_dir.glob("*.md") if p.name != "SKILL.md")
    scripts = sorted(p.name for p in (skill_dir / "scripts").glob("*.py"))
    return json.dumps(
        {"skill": skill_name, "summary": _AVAILABLE_SKILLS[skill_name],
         "references": refs, "scripts": scripts, "content": content},
        ensure_ascii=False,
    )


def skill_summary() -> str:
    """skill 摘要(注入 w1 澄清系统提示,渐进式披露的摘要层)。"""
    return json.dumps(_AVAILABLE_SKILLS, ensure_ascii=False)


def structured_with_skill(llm, schema, messages):
    """结构化输出 + load_skill 工具绑定(最多 2 回合,对照 sentiment 工具循环)。

    真实模型:with_structured_output(schema, tools=[load_skill], include_raw=True)
    —— json_schema 模式官方 tools 参数,输出 schema 与 load_skill 同时下发;
    模型回合 1 调 load_skill → 执行并喂回 ToolMessage → 回合 2 出 schema JSON。
    parsed 仍为空(模型又调工具/解析失败)→ 返回 None → worker 既有确定性骨架降级。

    不传 strict:worker 输出 schema 含默认值字段(QuestionsOutput.questions 等),
    OpenAI strict json_schema 禁止默认值,传了会被 API 拒绝;load_skill 单字符串
    参数无需 strict。若后续供应商支持 strict 且 schema 无默认值,可再开启。

    脚本/fake LLM(无 bind_tools):跳过工具绑定,with_structured_output(schema)
    原样返回(既有测试契约不变,ScriptedLLM 等 fake 不感知工具)。

    Args:
        llm: 聊天模型或 fake(可为 None → 返回 None)。
        schema: 结构化输出 pydantic 契约(QuestionsOutput 等)。
        messages: ChatPromptTemplate.format_messages 产物。

    Returns:
        schema 实例;失败/不可用 → None(worker 走确定性骨架)。
    """
    if llm is None:
        return None
    try:
        if hasattr(llm, "bind_tools"):
            try:
                structured = llm.with_structured_output(
                    schema, tools=[load_skill], include_raw=True)
            except TypeError:
                structured = None  # 实现不支持 tools 参数 → 普通结构化输出
            if structured is not None:
                result = structured.invoke(messages)
                if isinstance(result, dict) and "raw" in result:
                    raw = result.get("raw")
                    if getattr(raw, "tool_calls", None):
                        tool_msgs = [
                            ToolMessage(content=load_skill.invoke(tc["args"]),
                                        tool_call_id=tc["id"])
                            for tc in raw.tool_calls
                        ]
                        result = structured.invoke([*messages, raw, *tool_msgs])
                    return result.get("parsed")
                return result  # 非 include_raw 形态(自定义实现):直接返回
        return llm.with_structured_output(schema).invoke(messages)
    except Exception:
        return None  # LLM 故障 → worker 既有确定性骨架
