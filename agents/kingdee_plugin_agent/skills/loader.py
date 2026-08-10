"""load_skill 工具:skill 渐进式披露加载(对照 sentiment-query-agent 模式)。

设计对照:agents/sentiment_query_agent/skills/loader.py(sentiment 6 步流水线)。

- agent 启动只加载 skill 摘要(_AVAILABLE_SKILLS,注入系统提示)。
- worker 需要时调 load_skill 取完整内容(SKILL.md + 类型模板)。
- 查找顺序:agents/kingdee_plugin_agent/skills/ → common/skills/。
- references 形态两种兼容:老形态(requirement-clarify)类型模板直接放
  skill 目录;新形态(design-builder 等 4 个方法论 skill)放 references/ 子目录,
  load_skill 两种都收,返回的 references 字段是 name→content 映射
  (模板正文全量交付 —— agent 的 LLM 没有文件工具,只给文件名等于没给);
  scripts 恒为空列表(无分步脚本)。
- structured_with_skill:结构化输出 + load_skill 绑定(官方 tools 参数,
  langchain-openai 1.4.1 源码实测核对 —— bind_tools 后再 with_structured_output
  会经 __getattr__ 委派丢失 tools,必须用 with_structured_output(schema,
  tools=[load_skill], include_raw=True) 形态),脚本/fake LLM(无 bind_tools)
  自动跳过绑定,既有测试契约不变。
- 首选形态被拒(2026-08-10 真实 DeepSeek 实测):langchain-openai 的 tools 参数
  在 strict=None(不传 strict)时生成的工具不带 strict 标记,openai SDK
  validate_input_tools 本地校验拒绝(ValueError: `load_skill` is not strict);
  传 strict=True 又会被 DeepSeek API 拒绝(400 This response_format type is
  unavailable now,json_schema response_format 不支持)→ invoke 抛异常时自动
  回退 JSON Mode(bind_tools([load_skill], strict=True) + response_format
  json_object + schema 契约指令注入系统提示 + 手动 pydantic 解析,真实 DeepSeek
  实测可用,见 CLAUDE.md「load_skill 绑定未线上验证」回退方案)。回退保持同一
  外部契约:畸形 JSON 同输入重试 1 次(共 2 次尝试)、工具 2 回合上限、失败 → None。
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.output_parsers import PydanticOutputParser
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
    "design-builder": (
        "金蝶插件设计方法论(w2):基于需求确认摘要产出 design.md —— 事件绑定决策"
        "(操作+时机)、控件与字段映射(元数据真实字段/读写方向)、拦截方式"
        "(硬/软拦截+文案)、联动单据、异常骨架,类型检查清单逐项落实,"
        "输出固定骨架 + 验收自检(w1 决策逐条对照)"
    ),
    "code-generator": (
        "金蝶插件 C# 代码生成方法论(w3):模板优先(templates/<type>/template.cs"
        "骨架不变,业务只填 {{BUSINESS_LOGIC}})、指南参数化(字段/操作/API 签名"
        "必须真实,无来源外部 API 一律 TODO 占位,禁止编造)、冲突以模板为准"
        "(注释标注)、占位符清零,输出可编译 Plugin.cs"
    ),
    "code-reviewer": (
        "金蝶插件代码审查方法论(w4):规范库整库逐条对照 + API 抽查(事件签名/"
        "using 引用)+ 模板基线比对,findings 契约 severity/line/issue/依据/修法,"
        "裁决规则:Critical 或 Important → Needs fixes,仅 Minor → Approved"
    ),
    "compile-fixer": (
        "金蝶编译修复方法论(w5):错误分类(缺引用/签名不匹配/拼写/命名空间/"
        "占位符残留)→ 经验库检索(命中修复后自核)→ 修复优先级(先阻断性)"
        "→ 重编纪律 5 轮 + 防重复提交;环境类错误(C# 6 语法需 Roslyn / 框架"
        "不匹配 / 编译超时)不修代码,报告 BLOCKED 提示运维;具体错误映射"
        "单一来源为经验库(启动种子 + w7 沉淀),skill 只含方法论"
    ),
    "knowledge-steward": (
        "金蝶知识库全生命周期方法论(w7 沉淀 + 人工维护 + 检索路由):沉淀标准"
        "(可复现错误模式/条目格式/proposed 必带验证建议/签名去重/"
        "proposed→verified/不阻塞纪律)+"
        "维护手册(种子增补幂等/文档导入/规范库合并 8k 预算超限转检索/定期 review)"
        "+ 检索路由速查表(api_ref/guide/experience × w2-w5,bm25_weight 0.7 约定、"
        "L2 低=好 vs RRF 高=好的分数方向);w7 为确定性代码,本文供人工/未来 LLM 化参照"
    ),
}

#: 每步注入的 load_skill 提示(告诉 LLM 可调工具拿方法论,对照 sentiment 方案 2a)
SKILL_HINT = (
    "\n\n可用工具: load_skill(skill_name)。金蝶插件方法论按阶段加载:"
    "需求澄清(requirement-clarify)/设计(design-builder)/生成(code-generator)/"
    "审查(code-reviewer)/编译修复(compile-fixer)/知识沉淀(knowledge-steward),"
    "需要时调用 load_skill 获取专业指导,工具返回内容仅供参考,不改变输出格式。"
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
    """按需加载 skill 完整内容(SKILL.md + 类型模板正文)。

    references 为 name→content 映射(如 {"bill.md": "<模板全文>"}):
    LLM 没有文件工具,模板正文必须随 JSON 交付,不能只给文件名。

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
    # references 两种形态:老形态类型模板 .md 直接放 skill 目录;
    # 新形态(design-builder 等)放 references/ 子目录 —— 都收进 name→content
    # 映射(模板正文是模型拿到的实质内容)
    refs = {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(skill_dir.glob("*.md"))
        if p.name != "SKILL.md"
    }
    refs.update({
        p.name: p.read_text(encoding="utf-8")
        for p in sorted((skill_dir / "references").glob("*.md"))
    })
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

    真实模型双形态(首选被拒自动回退,2026-08-10 真实 DeepSeek 实测):
      首选:with_structured_output(schema, tools=[load_skill], include_raw=True)
            —— json_schema 模式官方 tools 参数,输出 schema 与 load_skill 同时
            下发;模型回合 1 调 load_skill → 执行并喂回 ToolMessage → 回合 2
            出 schema JSON。此形态在 DeepSeek 上 invoke 即被拒(不传 strict:
            openai SDK 本地校验;传 strict=True:API 400 不支持该 response_format),
            异常时自动走回退。
      回退:JSON Mode(bind_tools([load_skill], strict=True) + response_format
            json_object)+ schema 格式指令注入系统提示 + 手动 pydantic 解析
            (真实 DeepSeek 实测可用,load_skill 工具回合 2 回合上限一致)。
    两形态同一外部契约:畸形 JSON 同输入重试 1 次(共 2 次尝试);工具回合后的
    结果直接返回(成功出 schema / 又调工具强制停止);重试与工具 2 回合上限
    正交。失败 → None → worker 既有确定性骨架降级。

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
                try:
                    return _run_structured_tool_rounds(structured, messages)
                except Exception:
                    # 首选形态 invoke 被拒(openai SDK strict 校验 / DeepSeek
                    # json_schema 不支持)→ JSON Mode 回退(CLAUDE.md 约束段)
                    return _run_json_mode_rounds(llm, schema, messages)
        return llm.with_structured_output(schema).invoke(messages)
    except Exception:
        return None  # LLM 故障 → worker 既有确定性骨架


def _run_structured_tool_rounds(structured, messages):
    """首选形态:with_structured_output(schema, tools, include_raw) 工具回合。

    畸形 JSON 重试(设计 §8):parsed=None 且无 tool_calls(解析失败)→
    同输入重试 1 次(共 2 次尝试),仍失败返回 None → worker 既有确定性
    骨架降级。重试与工具 2 回合上限正交:工具回合后的结果直接返回
    (成功出 schema / 又调工具强制停止),不再参与解析重试。
    """
    for _ in range(2):
        result = structured.invoke(messages)
        if not (isinstance(result, dict) and "raw" in result):
            return result  # 非 include_raw 形态(自定义实现):直接返回
        raw = result.get("raw")
        if getattr(raw, "tool_calls", None):
            tool_msgs = [
                ToolMessage(content=load_skill.invoke(tc["args"]),
                            tool_call_id=tc["id"])
                for tc in raw.tool_calls
            ]
            result = structured.invoke([*messages, raw, *tool_msgs])
            return result.get("parsed")
        parsed = result.get("parsed")
        if parsed is not None:
            return parsed
        # parsed=None 且无 tool_calls:畸形 JSON → 同输入重试
    return None


def _parse_json_content(parser, response):
    """JSON Mode 响应 → schema 实例;工具调用/空内容/畸形 JSON → None。

    畸形 JSON 与空内容都返回 None(调用方按解析失败重试/降级处理)。
    """
    if getattr(response, "tool_calls", None):
        return None  # 仍调工具:2 回合上限强制停止
    content = getattr(response, "content", None)
    if not content:
        return None
    try:
        return parser.parse(content)
    except Exception:
        return None


def _run_json_mode_rounds(llm, schema, messages):
    """JSON Mode 回退(CLAUDE.md 约束段):bind_tools(strict=True) + json_object。

    首选形态 invoke 被拒时启用 —— 2026-08-10 真实 DeepSeek 实测:不传 strict
    生成的工具被 openai SDK 本地校验拒绝(ValueError not strict);传 strict=True
    则 json_schema response_format 被 API 拒绝(400 This response_format type is
    unavailable now)。JSON Mode 无响应格式限制,DeepSeek 实测可用(回合 1 调
    load_skill → 执行喂回 → 回合 2 出合法 JSON)。

    与首选形态同一外部契约:工具 2 回合上限、畸形 JSON 同输入重试 1 次
    (共 2 次尝试)、工具回合后的结果直接返回(不参与解析重试)、失败 → None
    (worker 确定性骨架降级)。
    """
    parser = PydanticOutputParser(pydantic_object=schema)
    bound = llm.bind_tools([load_skill], strict=True).bind(
        response_format={"type": "json_object"})
    # JSON Mode 无响应格式限制,模型输出契约靠系统提示给出:
    # schema 的 JSON 格式指令注入首条 system 消息(纯文本指令,无模板冲突)
    msgs = [
        SystemMessage(content=f"{messages[0].content}\n\n{parser.get_format_instructions()}")
        if i == 0 else m
        for i, m in enumerate(messages)
    ]
    for _ in range(2):  # 畸形 JSON 重试:同输入重试 1 次(共 2 次尝试)
        r = bound.invoke(msgs)
        if getattr(r, "tool_calls", None):
            tool_msgs = [
                ToolMessage(content=load_skill.invoke(tc["args"]),
                            tool_call_id=tc["id"])
                for tc in r.tool_calls
            ]
            # 工具回合后的结果直接返回(成功出 schema / 又调工具强制停止 None)
            return _parse_json_content(parser, bound.invoke([*msgs, r, *tool_msgs]))
        parsed = _parse_json_content(parser, r)
        if parsed is not None:
            return parsed
    return None  # 2 次尝试均畸形 → 降级
