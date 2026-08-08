"""w1 需求澄清:交互式,一次一问,LLM 提问/拆解,spec+plan 双产物 JSON 落盘。

交互流(agent.py w1 节点驱动,每轮一次 interrupt):
  生成问题清单 → 逐问 interrupt()/答 →(上限 MAX_ROUNDS 轮)→ 确认摘要
  interrupt()/确认 → 拆子任务(plugin_types + deps)→ spec.json + plan.json 落盘

LLM 契约:
  - generate_questions:  QuestionOutput(questions)  — 问题清单(一次生成,防 resume 重算)
  - split_subtasks:      PlanOutput(subtasks)       — 拆解(plugin_type + deps)
  llm=None 时确定性兜底:1 个默认问题 / 按 spec.plugin_types(缺省 bill)拆单子任务。

挂起注意(铁律,经安装包核实):interrupt() 所在节点 resume 时会整体重跑,
interrupt 的 payload 必须由 state 确定性得出 —— 问题清单存 state.clarify_questions,
确认摘要由已记录答案生成,均不依赖 LLM 重算。
"""
import json

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from agents.kingdee_plugin_agent.graph.state import Subtask
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase
from agents.kingdee_plugin_agent.skills.loader import (
    SKILL_HINT,
    skill_summary,
    structured_with_skill,
)

DEFAULT_QUESTION = "请描述该插件的核心业务场景与目标单据(FormId),以及期望的关键行为。"

#: 确认词(大小写不敏感)
_CONFIRM_WORDS = {"y", "yes", "ok", "确认", "同意", "可以", "好的", "是", "没问题"}


def is_confirmed(answer) -> bool:
    if not isinstance(answer, str):
        return False
    return answer.strip().lower() in _CONFIRM_WORDS


def build_confirmation_summary(spec: dict) -> str:
    lines = ["## 需求确认摘要"]
    lines.append("### 已确认决策")
    for d in spec.get("decisions", []):
        lines.append(f"- {d['q']}: {d['a']}")
    lines.append("### 假设(你没说的,我按此处理,不认可请指出)")
    for a in spec.get("assumptions", []):
        lines.append(f"- {a}")
    return "\n".join(lines)


class QuestionsOutput(BaseModel):
    """w1 澄清问题清单(结构化输出契约)。"""

    questions: list[str] = Field(default_factory=list)


class PlanItem(BaseModel):
    """拆解出的单个子任务。"""

    id: str = ""
    plugin_type: str = "bill"        # bill | service | list
    title: str = ""
    deps: list[str] = Field(default_factory=list)


class PlanOutput(BaseModel):
    """w1 拆解输出:子任务清单(plugin_types + deps)。"""

    subtasks: list[PlanItem] = Field(default_factory=list)


class RequirementWorker(WorkerBase):
    name = "w1"
    MAX_ROUNDS = 10

    # ── 澄清循环接口(agent.py w1 节点调用) ────────────────────────────

    def generate_questions(self, state) -> list[str]:
        """生成澄清问题清单(一次生成存 state,resume 不重算)。"""
        if self.llm is None:
            return [DEFAULT_QUESTION]
        try:
            # 注入 skill 摘要 + load_skill 提示:LLM 可主动调工具拿问题模板。
            # 摘要 JSON 含花括号,必须走模板变量占位(dev-standards §7.2 f-string 陷阱)
            prompt = ChatPromptTemplate.from_messages([
                ("system", self._load_prompt("w1_requirement.md") + SKILL_HINT
                 + "可用 skill 摘要:\n{skill_summary}"),
                ("human", "需求:\n{req}\n\n请输出最多 {n} 个澄清问题,一次一问。"),
            ])
            req = json.dumps(state.requirement_spec, ensure_ascii=False)[:1500]
            out = structured_with_skill(
                self.llm, QuestionsOutput,
                prompt.format_messages(req=req, n=self.MAX_ROUNDS,
                                       skill_summary=skill_summary()))
            if out and out.questions:
                return list(out.questions)[:self.MAX_ROUNDS]
        except Exception:
            pass  # LLM 故障 → 默认问题
        return [DEFAULT_QUESTION]

    def interrupt_message(self, state) -> str:
        """当前轮 interrupt() 的 payload:问题 或 确认摘要(由 state 确定性生成)。"""
        if state.clarify_round < len(state.clarify_questions):
            return state.clarify_questions[state.clarify_round]
        return build_confirmation_summary(self.build_spec(state))

    def record_answer(self, state, answer) -> None:
        """记录本轮答复(原地追加;节点把 clarify_answers 整体回写)。"""
        state.clarify_answers.append(str(answer))

    def build_spec(self, state) -> dict:
        """由已记录答案构建 spec:decisions + assumptions(确认摘要/落盘用)。"""
        decisions = []
        for i, ans in enumerate(state.clarify_answers):
            q = state.clarify_questions[i] if i < len(state.clarify_questions) else f"问题{i + 1}"
            decisions.append({"q": q, "a": ans})
        assumptions = list(state.clarify_feedback)
        if not assumptions:
            assumptions.append("未说明的细节按金蝶 BOS 默认规范处理")
        return {
            "requirement": state.requirement_spec.get("requirement", ""),
            "decisions": decisions,
            "assumptions": assumptions,
        }

    def split_subtasks(self, state, spec: dict) -> list[Subtask]:
        """拆解子任务:LLM 产出(plugin_types + deps),失败回退确定性拆分。"""
        if self.llm is not None:
            try:
                prompt = ChatPromptTemplate.from_messages([
                    ("system", self._load_prompt("w1_requirement.md") + SKILL_HINT),
                    ("human", "需求规格:\n{spec}\n\n请拆解为子任务清单(plugin_type 限 bill/service/list)。"),
                ])
                out = structured_with_skill(self.llm, PlanOutput,
                                            prompt.format_messages(spec=json.dumps(spec, ensure_ascii=False)))
                if out and out.subtasks:
                    return [
                        Subtask(id=it.id or f"{chr(65 + i)}1",
                                plugin_type=it.plugin_type, title=it.title or "插件子任务",
                                deps=list(it.deps))
                        for i, it in enumerate(out.subtasks)
                    ]
            except Exception:
                pass  # LLM 故障 → 确定性拆分
        return self._split_fallback(spec)

    def _split_fallback(self, spec: dict) -> list[Subtask]:
        """确定性兜底:按 spec.plugin_types(缺省 bill)拆单子任务。"""
        types = spec.get("plugin_types") or ["bill"]
        title = spec.get("requirement", "插件")[:30]
        return [Subtask(id=f"{chr(65 + i)}1", plugin_type=t, title=f"{title}({t})", deps=[])
                for i, t in enumerate(types)]

    def persist(self, spec: dict, todo: list[Subtask]) -> None:
        """spec + plan 落盘 JSON(非 repr,终审 C5 修复)。"""
        self.store.write("requirement", "spec.json",
                         json.dumps(spec, ensure_ascii=False, indent=2))
        self.store.write("requirement", "plan.json",
                         json.dumps([t.__dict__ for t in todo], ensure_ascii=False, indent=2))

    # ── 基类契约兼容(_execute,单测/直连调用) ───────────────────────────

    def _execute(self, state, subtask) -> dict:
        spec = getattr(state, "requirement_spec", {}) or {}
        path = self.store.write("requirement", "spec.json",
                                json.dumps(spec, ensure_ascii=False, indent=2))
        return {"status": "DONE", "artifact_key": "", "path": str(path),
                "evidence": f"spec 落盘: {path}", "concerns": ""}
