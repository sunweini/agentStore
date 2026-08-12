#!/usr/bin/env python
"""load_skill 绑定线上验证:w1 generate_questions 真实 DeepSeek smoke(一次性脚本,不入测试)。

Task 2(sdd 2026-08-10 kingdee-plugin-agent-four-fixes):验证
agents/kingdee_plugin_agent/skills/loader.py 的 structured_with_skill
(with_structured_output(schema, tools=[load_skill], include_raw=True) 官方
tools 参数形态)在真实 DeepSeek 上的行为。CLAUDE.md「load_skill 绑定未线上验证」
约束段:被 API 拒绝 → 回退 sentiment JSON Mode 模式。

观测点:
  1. API 是否拒绝 tools + 结构化输出组合(w1-w5 全部依赖此组合)
  2. LLM 是否真的调 load_skill(工具 2 回合循环是否生效)
  3. 结构化输出是否正常解析(parsed 非空)
  4. 畸形 JSON / 异常 → 观察降级路径(DEFAULT_QUESTION / None)

2026-08-10 实测结论:
  首选形态 invoke 被拒 —— 不传 strict:openai SDK 本地校验 ValueError
  (`load_skill` is not strict, Only strict function tools can be auto-parsed,
  openai/lib/_parsing/_completions.py validate_input_tools);
  传 strict=True:DeepSeek API 400「This response_format type is unavailable now」。
  → loader 已实现 JSON Mode 回退(bind_tools(strict=True) + json_object),
  本脚本回归验证回退路径真实可用。

用法: .venv/bin/python scripts/smoke_structured_with_skill.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.prompts import ChatPromptTemplate

from common.llm import get_chat_model
from agents.kingdee_plugin_agent.graph.state import TaskState
from agents.kingdee_plugin_agent.graph.workers.w1_requirement import (
    DEFAULT_QUESTION,
    RequirementWorker,
)
from agents.kingdee_plugin_agent.skills.loader import SKILL_HINT, skill_summary
from agents.kingdee_plugin_agent.store.artifact_store import ArtifactStore

REQ = {
    "requirement": "给采购单审核加库存校验:采购订单审核时校验物料可用库存量,不足则拦截并提示。",
    "env_name": "test",
}


def main() -> int:
    print("== smoke: load_skill 绑定线上验证(w1 generate_questions 真实 DeepSeek)==")
    print("== 首选形态已被拒(openai SDK strict 校验 / API 400)→ 验证 JSON Mode 回退 ==")
    t0 = time.time()

    store = ArtifactStore(root=Path("data/smoke-w1"))
    llm = get_chat_model()
    w1 = RequirementWorker(llm=llm, store=store)
    state = TaskState(requirement_spec=REQ)

    # ── Probe A:真实接线 —— RequirementWorker.generate_questions ──────────
    a_time = time.time()
    try:
        questions = w1.generate_questions(state)
    except Exception as exc:  # noqa: BLE001 —— 一次性观测脚本,兜住所有异常
        questions = None
        print(f"[A] generate_questions 抛出异常: {type(exc).__name__}: {exc}")
    print(f"[A] 耗时 {time.time() - a_time:.1f}s")
    print(f"[A] 返回问题数: {len(questions) if questions else 0}")
    if questions and questions != [DEFAULT_QUESTION]:
        for i, q in enumerate(questions, 1):
            print(f"[A]   Q{i}: {q}")
    else:
        print(f"[A]   降级(默认问题) → 回退路径失败,需检查 loader 日志")

    # ── Probe B:直连真实 LLM 复刻 loader 回退路径 ─────────────────────────
    print("\n-- Probe B:JSON Mode 回退路径直连验证(bind_tools + json_object)--")
    prompt = ChatPromptTemplate.from_messages([
        ("system", w1._load_prompt("w1_requirement.md") + SKILL_HINT
         + "可用 skill 摘要:\n{skill_summary}"),
        ("human", "需求:\n{req}\n\n请输出最多 {n} 个澄清问题,一次一问。"),
    ])
    messages = prompt.format_messages(
        req=json.dumps(REQ, ensure_ascii=False)[:1500],
        n=w1.MAX_ROUNDS,
        skill_summary=skill_summary(),
    )

    # 直接调 structured_with_skill(内部首选被拒 → 自动回退)
    from agents.kingdee_plugin_agent.graph.workers.w1_requirement import QuestionsOutput
    from agents.kingdee_plugin_agent.skills.loader import structured_with_skill

    b_time = time.time()
    out = structured_with_skill(llm, QuestionsOutput, messages)
    print(f"[B] structured_with_skill 返回耗时 {time.time() - b_time:.1f}s")
    if out is None:
        print("[B] 返回 None → 回退路径失败(需检查异常)")
        return 3
    print(f"[B] 成功: QuestionsOutput(questions={out.questions!r})")
    print(f"\n总耗时 {time.time() - t0:.1f}s")
    return 0 if (questions and questions != [DEFAULT_QUESTION]) else 4


if __name__ == "__main__":
    sys.exit(main())
