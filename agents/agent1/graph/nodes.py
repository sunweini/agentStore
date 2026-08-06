"""6 步流水线节点:每步 websearch → LLM 生成 → 调 skill 分步脚本按格式传回。

设计见 docs/superpowers/specs/2026-08-06-agent1-sentiment-query-agent-design.md §3/§5.1。

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

from agents.agent1.graph.state import AgentState
from agents.agent1.tools.websearch import websearch
from common import config
from common.llm import get_chat_model
from common.otel import get_tracer

logger = logging.getLogger(__name__)

_AGENT = "agent1"
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

# 每步系统指令(该步做什么 + 输出格式;格式细节在 skill output-formats.md)
_STEP_PROMPTS = {
    1: "你是实体测绘专家。基于用户提供的公司名,识别母公司、海外法人(按语区分组)、"
       "拼写变体、同名干扰源。先 websearch 验证,再输出 JSON(格式见 skill output-formats.md)。",
    2: "你是主体画像专家。基于实体测绘结果,判定主体角色(承包商/业主/AI判定),"
       "建立 direct/indirect/context 相关度口径,识别重点地区。先 websearch 验证,再输出 JSON。",
    3: "你是关键词字典专家。基于实体测绘与画像,构建 A/B/C/D/R/X 六层关键词字典,"
       "短缩写强制 context_guard。先 websearch 验证同音干扰,再输出 JSON。",
    4: "你是检索式构建专家。基于关键词字典,按国别×项目群分组,写双轨(布尔+Google)检索式。"
       "先 websearch 验证,再输出 JSON。",
    5: "你是属地信源专家。为每轨配属地信源白名单域名(属地媒体/判例库/政府/NGO)。"
       "先 websearch 验证域名活性,再输出 JSON。",
    6: "你是频次定级专家。按信号为每轨定频次(快讯/日/周/双周/月)与风险等级。"
       "先 websearch 验证时效,再输出 JSON。",
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
        # 1. 该步系统指令
        prompt = ChatPromptTemplate.from_messages([
            ("system", _STEP_PROMPTS[step]),
            ("human", "公司名:{company}\n上步产物:{prev}\n搜索:\n{search}\n按格式输出 JSON"),
        ])

        # 2. websearch 搜索(每步搜,验证 + 素材)
        company = group.get("company_name", "")
        search_result = await websearch(f"{company} 海外 项目 负面 舆情", engine="auto")
        logger.info("service=%s step=%d span=%s event=search_done", _AGENT, step, span_id)

        # 3. LLM 生成(强制 JSON,重试 2 次)
        llm = get_chat_model()
        raw_output: dict | None = None
        for attempt in range(3):
            messages = prompt.format_messages(
                company=company,
                prev=json.dumps(group.get(f"_step{step-1}", {}), ensure_ascii=False),
                search=search_result,
            )
            response = llm.invoke(messages)
            content = response.content if isinstance(response, AIMessage) else str(response)
            try:
                raw_output = json.loads(content)
                break
            except json.JSONDecodeError:
                if attempt < 2:
                    logger.warning("service=%s step=%d span=%s event=retry reason=bad_json",
                                   _AGENT, step, span_id)
                else:
                    raise RuntimeError("LLM 输出非 JSON,重试 2 次仍失败")

        # 4. skill 脚本标准化
        normalized = _run_skill_script(step, raw_output)
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
                        tr["frequency"] = cd.get("tracks", [])[t].get("frequency", "周级")
                        tr["risk"] = cd.get("tracks", [])[t].get("risk", "medium")
                        tr["relevance"] = cd.get("tracks", [])[t].get("relevance", "direct")

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
