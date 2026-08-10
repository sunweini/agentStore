"""6 步流水线节点:每步 websearch → LLM 生成 → 调 skill 分步脚本按格式传回。

设计见 docs/superpowers/specs/2026-08-06-sentiment-query-agent-sentiment-query-agent-design.md §3/§5.1。

每步统一模式:
1. 加载该步 prompt(graph 内联 system 指令,引用 skill references/output-formats.md 格式)
2. websearch 搜索(gateway MCP 池)
3. LLM 生成(强制输出 JSON)
4. 调 skill 分步脚本 stepN.py 标准化 → 写回 state

职责分离:
- load_skill 取方法论知识(喂 LLM 理解怎么干)
- stepN.py 只做格式契约(LLM 原始输出 → 固定 JSON)
- LLM 输出非 JSON → 脚本返回格式错误,节点重试 2 次
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate

from agents.sentiment_query_agent.graph.state import AgentState
from agents.sentiment_query_agent.tools.websearch import websearch
from common import config
from common.llm import get_chat_model
from common.otel import get_tracer

logger = logging.getLogger(__name__)

_AGENT = "sentiment-query-agent"

# 每步注入的 load_skill 提示:告诉 LLM 可调工具拿方法论(方案 2a)
_SKILL_HINT = (
    "\n\n可用工具: load_skill(skill_name)。本步涉及海外舆情方法论"
    "(六层词表/双轨语法/信源/频次规则),需要时调用 load_skill 获取专业指导,"
    "然后按格式输出 JSON。工具返回内容仅供参考,不改变输出格式。"
)


def _extract_json(text: str) -> dict | None:
    """容错解析 LLM 输出中的 JSON。

    DeepSeek 常在 JSON 外包 Markdown 代码块或说明文字,直接 json.loads 会失败。
    策略:
    1. 剥 ```json ... ``` 代码块
    2. 截取首个 { 到最后一个 } 的子串
    3. json.loads;失败返回 None
    """
    if not text:
        return None
    content = text.strip()
    # 1. 剥代码块
    if content.startswith("```"):
        content = content.split("```", 2)[1] if "```" in content[3:] else content[3:]
        content = content.strip()
        if content.startswith("json"):
            content = content[4:].strip()
    # 2. 截取首个 { 到最后一个 }
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
# skill 分步脚本目录(项目内,agent 专属)
_SKILL_SCRIPTS = (
    Path(__file__).resolve().parent.parent
    / "skills" / "overseas-sentiment-query-builder" / "scripts"
)

# 6 步:步骤名 → (分步脚本, 该步 skill 参考文件)
STEPS = {
    1: ("step1_entities.py", "SKILL.md"),
    2: ("step2_profile.py", "SKILL.md"),
    3: ("step3_keywords.py", "keyword-dictionary.md"),
    4: ("step4_queries.py", "query-patterns.md"),
    5: ("step5_sources.py", "source-whitelists.md"),
    6: ("step6_cadence.py", "cadence-and-risk.md"),
}

# 每步系统指令(该步做什么 + 输出格式样例;格式契约见 skill references/output-formats.md)
# 注意:
# 1. prompt 必须给 LLM 具体 JSON schema 样例,否则 LLM 盲猜字段导致脚本校验失败。
# 2. JSON 样例的 {} 必须转义为 {{}} —— ChatPromptTemplate 用 f-string 语法,
#    未转义的花括号会被当作模板变量(报 "Nested replacement fields")。
_STEP_PROMPTS = {
    1: "你是实体测绘专家。基于用户提供的公司名,识别母公司、海外法人(按语区分组)、"
       "拼写变体、同名干扰源。先 websearch 验证,再输出 JSON。\n"
       "输出格式(JSON):\n"
       '{{"entities": {{"parent": "母公司名", "subsidiaries": ["子公司"], '
       '"overseas_entities": [{{"name": "海外法人名", "lang": "en", "region": "赞比亚"}}], '
       '"spelling_variants": ["拼写变体"], "interference_sources": ["同名干扰源"]}}}}',
    2: "你是主体画像专家。基于实体测绘结果,判定主体角色(承包商/业主/ai判定),"
       "建立相关度口径,识别重点地区。先 websearch 验证,再输出 JSON。\n"
       "输出格式(JSON):\n"
       '{{"profile": {{"role": "承包商", "relevance_rules": {{"direct": "", "indirect": "", "context": ""}}, '
       '"regions": ["重点地区"]}}}}',
    3: "你是关键词字典专家。构建 A/B/C/D/R/X 六层关键词字典,短缩写强制 context_guard。"
       "先 websearch 验证同音干扰,再输出 JSON。\n"
       "输出格式(JSON,keywords 数组,每项一层):\n"
       '{{"keywords": [{{"layer": "A", "category": "A1集团/公司名称簇", '
       '"terms": "\\"中文全称\\" \\"ABBR\\"", "lang": "全", "guard": "", "note": ""}}]}}',
    4: "你是检索式构建专家。基于关键词字典,按国别×项目群分组,写双轨(布尔+Google)检索式。"
       "先 websearch 验证,再输出 JSON。\n"
       "重要:每轨的 key 字段只允许这 6 个值之一: 全量新闻 负面新闻 行业新闻 快讯 司法 招标。"
       "boolean 字段是检索式本身,不是 key。\n"
       "输出格式(JSON,schemes 数组,每项含 tracks 数组):\n"
       '{{"schemes": [{{"id": "Q0", "name": "集团层", "region": "全语种", "lang": "中/英", '
       '"desc": "", "gaps": [], "tracks": [{{"key": "全量新闻", "boolean": "(...)", "google": "(...)"}}]}}]}}',
    5: "你是属地信源专家。为每轨配属地信源白名单域名(属地媒体/判例库/政府/NGO)。"
       "先 websearch 验证域名活性,再输出 JSON。\n"
       "输出格式(JSON,schemes 结构与步骤 4 对应):\n"
       '{{"schemes": [{{"id": "Q0", "tracks": [{{"key": "全量新闻", "sources": ["属地媒体.com"]}}]}}]}}',
    6: "你是频次定级专家。按信号为每轨定频次与相关度。先 websearch 验证时效,再输出 JSON。\n"
       "输出格式(JSON,schemes 结构与步骤 4 对应):\n"
       '{{"schemes": [{{"id": "Q0", "tracks": [{{"key": "全量新闻", "frequency": "周级", '
       '"relevance": "direct"}}]}}]}}',
}


def _run_skill_script(step: int, raw_output: dict) -> dict:
    """调 skill 分步脚本,把 LLM 原始输出标准化成固定格式 JSON。

    脚本职责:校验字段、标准化、补默认值、缺字段记 GAP。
    脚本不存在/执行失败 → 抛 RuntimeError(节点捕获标 error)。
    """
    script = _SKILL_SCRIPTS / STEPS[step][0]
    if not script.exists():
        raise RuntimeError(f"skill 分步脚本不存在: {script}")
    proc = subprocess.run(
        [config.get_env("PYTHON", "python3"), str(script)],
        input=json.dumps(raw_output, ensure_ascii=False),
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"skill 脚本 {script.name} 失败: {proc.stderr[:500]}")
    return json.loads(proc.stdout)


async def _step_node(state: AgentState, step: int) -> AgentState:
    """单步执行:websearch → LLM → skill 脚本标准化 → 写 state。"""
    tracer = get_tracer()
    span_id = uuid.uuid4().hex[:8]
    start = time.monotonic()
    logger.info("service=%s step=%d span=%s event=step_start", _AGENT, step, span_id)

    group = dict(state.get("group", {}))
    step_status = list(group.get("step_status", []))
    step_status.append({"step": step, "status": "running", "output": None})

    try:
        # 1. 该步系统指令(含 load_skill 提示:LLM 可调工具拿方法论)
        prompt = ChatPromptTemplate.from_messages([
            ("system", _STEP_PROMPTS[step] + _SKILL_HINT),
            ("human", "公司名:{company}\n上步产物:{prev}\n搜索:\n{search}\n按格式输出 JSON"),
        ])

        # 2. websearch 搜索(每步搜,验证 + 素材)
        company = group.get("company_name", "")
        search_result = await websearch(f"{company} 海外 项目 负面 舆情", engine="auto")
        logger.info("service=%s step=%d span=%s event=search_done", _AGENT, step, span_id)

        # 3. LLM 生成(DeepSeek JSON Mode + load_skill 工具 + 容错解析,最多 2 回合)
        # - JSON Mode 强制模型输出合法 JSON(DeepSeek 官方,见 api-docs.deepseek.com/zh-cn/guides/json_mode)
        # - 绑定 load_skill 工具:LLM 需要方法论时主动调用(方案 2a,每步固定可用 skill 列表)
        # - 多轮:回合 1 调工具 → 回合 2 换无工具 LLM 生成 JSON
        #   原因:deepseek-v4-flash 带工具绑定时,工具回合后仍重复发 tool_calls
        #   且 content 为空(生产实测 2026-08-10),去掉工具绑定才能稳定出 JSON
        from agents.sentiment_query_agent.skills.loader import load_skill

        llm = get_chat_model().bind_tools([load_skill], strict=True).bind(
            response_format={"type": "json_object"}
        )
        llm_no_tools = get_chat_model().bind(response_format={"type": "json_object"})
        messages = prompt.format_messages(
            company=company,
            prev=json.dumps(group.get(f"_step{step-1}", {}), ensure_ascii=False),
            search=search_result,
        )
        normalized: dict | None = None
        for attempt in range(3):  # 外层:JSON 解析 + 格式校验重试(最多 3 次)
            response = await llm.ainvoke(messages)
            # 回合 1:LLM 发 tool_calls → 执行工具 → 回合 2(无工具 LLM,防 tool_calls 循环)
            if getattr(response, "tool_calls", None):
                tool_contents = "\n\n".join(
                    str(load_skill.invoke(tc["args"])) for tc in response.tool_calls
                )
                response = await llm_no_tools.ainvoke([
                    *messages,
                    HumanMessage(
                        content="load_skill 返回的方法论内容:\n" + tool_contents
                        + "\n\n已获取方法论。现在严格按格式直接输出 JSON,不要调用任何工具。"
                    ),
                ])
            content = response.content if isinstance(response, AIMessage) else str(response)
            raw_output = _extract_json(content)
            if raw_output is None:
                if attempt < 2:
                    logger.warning(
                        "service=%s step=%d span=%s event=retry reason=bad_json "
                        "tool_calls=%s content_len=%d content_head=%r",
                        _AGENT, step, span_id,
                        bool(getattr(response, "tool_calls", None)), len(content), content[:200],
                    )
                    # 带错误反馈重试:LLM 看到自己的坏输出 + 纠正指令
                    messages = [*messages, AIMessage(content=content), HumanMessage(
                        content="上面的输出不是合法 JSON。请严格按格式重新输出完整 JSON。")]
                    continue
                # 完整记录原始返回,便于排查模型输出问题(空 content = tool_calls 循环等)
                logger.error(
                    "service=%s step=%d span=%s event=bad_json_final "
                    "tool_calls=%s content_len=%d content_head=%r content_tail=%r",
                    _AGENT, step, span_id,
                    bool(getattr(response, "tool_calls", None)),
                    len(content), content[:500], content[-200:],
                )
                raise RuntimeError(
                    f"LLM 输出无法解析为 JSON,重试 2 次仍失败;"
                    f"content_len={len(content)}, head={content[:200]!r}"
                )
            # skill 脚本格式校验;失败同样带反馈重试(LLM 偶发缺字段/类型错)
            try:
                normalized = _run_skill_script(step, raw_output)
                break
            except RuntimeError as exc:
                if attempt == 2:
                    logger.error("service=%s step=%d span=%s event=format_error_final error=%s",
                                 _AGENT, step, span_id, exc)
                    raise
                logger.warning("service=%s step=%d span=%s event=retry reason=format_error error=%s",
                               _AGENT, step, span_id, exc)
                messages = [*messages, AIMessage(content=content), HumanMessage(
                    content=f"上面的输出格式校验失败:{exc}。请修正后重新输出完整 JSON。")]

        # 4. skill 脚本标准化产物(已在循环内校验通过)
        group[f"_step{step}"] = normalized

        # 5. 写回产物到对应字段
        if step == 1:
            group["entities"] = normalized
        elif step == 2:
            group["profile"] = normalized
        elif step == 3:
            group["keywords"] = normalized.get("keywords", [])
        elif step >= 4:
            group.setdefault("schemes", [])
            if step == 4:
                group["schemes"] = normalized.get("schemes", [])
            elif step == 5:
                for i, sc in enumerate(group["schemes"]):
                    src = normalized.get("schemes", [])[i] if i < len(normalized.get("schemes", [])) else {}
                    for t, tr in enumerate(sc.get("tracks", [])):
                        tr["sources"] = src.get("tracks", [])[t].get("sources", []) if t < len(src.get("tracks", [])) else []
            elif step == 6:
                for i, sc in enumerate(group["schemes"]):
                    cd = normalized.get("schemes", [])[i] if i < len(normalized.get("schemes", [])) else {}
                    for t, tr in enumerate(sc.get("tracks", [])):
                        cd_tracks = cd.get("tracks", []) if isinstance(cd, dict) else []
                        meta = cd_tracks[t] if t < len(cd_tracks) else {}
                        tr["frequency"] = meta.get("frequency", "周级")
                        tr["relevance"] = meta.get("relevance", "direct")

        step_status[-1] = {"step": step, "status": "done", "output": normalized}
        group["step_status"] = step_status
        logger.info("service=%s step=%d span=%s event=step_done duration_ms=%d",
                    _AGENT, step, span_id, int((time.monotonic() - start) * 1000))
        return {"group": group, "current_step": step}

    except Exception as exc:
        logger.error("service=%s step=%d span=%s event=step_error error=%s", _AGENT, step, span_id, exc)
        step_status[-1] = {"step": step, "status": "error", "output": None, "error": str(exc)}
        group["step_status"] = step_status
        return {"group": group, "current_step": step}


# 6 个节点函数(每个一步)
async def step1_node(state: AgentState) -> AgentState: return await _step_node(state, 1)
async def step2_node(state: AgentState) -> AgentState: return await _step_node(state, 2)
async def step3_node(state: AgentState) -> AgentState: return await _step_node(state, 3)
async def step4_node(state: AgentState) -> AgentState: return await _step_node(state, 4)
async def step5_node(state: AgentState) -> AgentState: return await _step_node(state, 5)
async def step6_node(state: AgentState) -> AgentState: return await _step_node(state, 6)
