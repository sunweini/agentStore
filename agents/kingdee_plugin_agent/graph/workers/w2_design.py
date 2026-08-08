"""w2 设计:类型分支配置表驱动,LLM + RAG + 经验库生成设计文档落盘。

LLM 契约:DesignOutput(design_markdown),输入 = w2_design.md + 类型分支要点
(TYPE_PROMPTS 指向 design-builder skill 的 references,方法论单源在 skills/)+
需求 spec + RAG 检索(guide 按插件类型过滤 + api_ref,终审 Plan B 确认 hybrid_search
支持相等过滤)+ 经验库检索(按子任务标题查历史踩坑,命中注入"历史踩坑参考"作
设计规避参考,verified 优先、非必须满足)。llm=None 或 LLM 失败 → 确定性骨架
(不阻塞流程)。

未知插件类型(终审 C6):返回 ERROR 上报而非裸 KeyError,由图包装器标记子任务 failed。
"""
import json

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase
from agents.kingdee_plugin_agent.skills.loader import SKILL_HINT, structured_with_skill

# 类型分支方法论单源在 skill references(design-builder),worker 直接读 skill 文件,
# 与 load_skill 交付同一来源(prompts/ 下不再有类型分支文件)
TYPE_PROMPTS = {"bill": "design-builder/references/bill.md",
                "service": "design-builder/references/service.md",
                "list": "design-builder/references/list.md"}
VALID_TYPES = tuple(TYPE_PROMPTS)


class DesignOutput(BaseModel):
    """w2 设计文档输出契约(经 with_structured_output 绑定)。"""

    design_markdown: str = ""


class DesignWorker(WorkerBase):
    name = "w2"

    def __init__(self, llm, store, rag=None, experience=None):
        super().__init__(llm, store)
        self.rag = rag
        self.experience = experience

    def _retrieve(self, collection: str, query: str, filter=None, k: int = 3) -> list[dict]:
        """RAG 检索(故障降级返回空,不阻塞设计)。"""
        if self.rag is None:
            return []
        try:
            return self.rag.hybrid_search(collection, query, k=k, filter=filter)
        except Exception:
            return []

    def _retrieve_experience(self, subtask) -> list[dict]:
        """设计时历史踩坑检索:按子任务标题查经验库,命中供设计规避参考。

        设计阶段错误码稀少(编译错误在 w5 才出现),故标题同时充当
        code-ish/message-ish 双信号:search_related(title, title, k=3)。
        返回 verified 优先(稳定排序,仅相对次序;经验库语义:种子/verified 可信,
        proposed 仅供参考,见 knowledge-steward 路由表)。故障降级返回空,
        不阻塞设计(与 _retrieve 同一纪律)。
        """
        if self.experience is None:
            return []
        try:
            hits = self.experience.search_related(subtask.title, subtask.title, k=3)
            return sorted(hits, key=lambda h: h["metadata"].get("confidence") != "verified")
        except Exception:
            return []

    def _llm_design(self, state, subtask, prompt: str) -> str | None:
        if self.llm is None:
            return None
        try:
            guide = self._retrieve("guide", subtask.title, filter={"plugin_type": subtask.plugin_type})
            api_ref = self._retrieve("api_ref", subtask.title)
            experience = self._retrieve_experience(subtask)
            context = json.dumps({
                "requirement_spec": state.requirement_spec,
                "title": subtask.title,
                "plugin_type": subtask.plugin_type,
                "guide": [g["text"] for g in guide],
                "api_ref": [a["text"] for a in api_ref],
                "experience": [
                    {"text": e["text"],
                     "status": e["metadata"].get("status") or "verified",
                     "confidence": e["metadata"].get("confidence", "verified")}
                    for e in experience
                ],
            }, ensure_ascii=False)
            human = "需求与检索上下文:\n{context}"
            if experience:
                # 历史踩坑参考:经验库命中仅作设计规避参考(非必须满足),verified 优先
                # 已排序;proposed 条目自核后采用,不得把参考条目当新增需求写进设计
                human += ("\n\ncontext 中 experience 为历史踩坑参考(经验库检索命中,"
                          "仅供参考、非必须满足):把已知错误模式转化为设计规避措施,"
                          "如签名级联 → 设计时核对基类事件签名;status=verified 可直接"
                          "遵循,status=proposed 自核后采用。")
            prompt = ChatPromptTemplate.from_messages([
                ("system", prompt + SKILL_HINT),
                ("human", human),  # JSON 走占位符,防 f-string 花括号冲突(dev-standards §7.2)
            ])
            out = structured_with_skill(self.llm, DesignOutput,
                                        prompt.format_messages(context=context))
            return out.design_markdown if out else None
        except Exception:
            return None  # LLM 故障 → 骨架,不阻塞

    def _execute(self, state, subtask) -> dict:
        base = self._load_prompt("w2_design.md")
        branch = TYPE_PROMPTS.get(subtask.plugin_type)
        if branch is None:
            return {"status": "ERROR", "artifact_key": "", "evidence": "",
                    "concerns": f"未知插件类型 {subtask.plugin_type!r}(合法: {VALID_TYPES}),子任务 {subtask.id} 标记失败"}
        prompt = base + "\n" + self._load_prompt(branch)
        design = self._llm_design(state, subtask, prompt)
        if design is None:
            # 确定性骨架(LLM 不可用/失败时保底):类型 + 设计要点
            design = f"# 设计:{subtask.title}\n类型:{subtask.plugin_type}\n{prompt}"
        path = self.store.write(subtask.id, "design.md", design)
        return {"status": "DONE", "artifact_key": "design_path", "path": str(path),
                "evidence": f"设计落盘: {path}", "concerns": ""}
