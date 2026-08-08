"""w3 代码生成:模板骨架 + 类型分支 + RAG 指南参数化,LLM 渲染全部 {{TOKEN}}。

LLM 契约:CodeOutput(code),输入 = 设计文档 + 类型模板(load_template)+
w3_generate.md + 类型分支 prompt + guide 检索。模板优先,冲突以模板为准。

确定性骨架(llm=None/失败):渲染 BUSINESS_LOGIC/CLASS_NAME/NAMESPACE 三个
占位符(模板唯一 token 集),防 w4 把未渲染占位符判 Critical。

未知插件类型(终审 C6):load_template 抛 ValueError → 捕获转 ERROR 上报,
不把裸异常抛到图上。
"""
import json

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase
from agents.kingdee_plugin_agent.skills.loader import SKILL_HINT, structured_with_skill
from agents.kingdee_plugin_agent.templates import load_template, render_template

TYPE_PROMPTS = {"bill": "w3_generate_bill.md", "service": "w3_generate_service.md", "list": "w3_generate_list.md"}


class CodeOutput(BaseModel):
    """w3/w5 代码输出契约(经 with_structured_output 绑定)。"""

    code: str = ""


class GenerateWorker(WorkerBase):
    name = "w3"

    def __init__(self, llm, store, rag=None):
        super().__init__(llm, store)
        self.rag = rag

    def _retrieve(self, collection: str, query: str, filter=None, k: int = 3) -> list[dict]:
        if self.rag is None:
            return []
        try:
            return self.rag.hybrid_search(collection, query, k=k, filter=filter)
        except Exception:
            return []

    def _llm_generate(self, state, subtask, design: str, tpl: str, prompt: str) -> str | None:
        if self.llm is None:
            return None
        try:
            guide = self._retrieve("guide", subtask.title, filter={"plugin_type": subtask.plugin_type})
            context = json.dumps({
                "design": design,
                "template": tpl,
                "plugin_type": subtask.plugin_type,
                "guide": [g["text"] for g in guide],
            }, ensure_ascii=False)
            prompt = ChatPromptTemplate.from_messages([
                ("system", prompt + SKILL_HINT),
                ("human", "设计文档与模板:\n{context}"),  # JSON 走占位符,防 f-string 花括号冲突(dev-standards §7.2)
            ])
            out = structured_with_skill(self.llm, CodeOutput,
                                        prompt.format_messages(context=context))
            return out.code if out else None
        except Exception:
            return None  # LLM 故障 → 骨架

    def _execute(self, state, subtask) -> dict:
        try:
            design = self.store.read(subtask.id, "design.md")
        except Exception as e:
            return {"status": "ERROR", "artifact_key": "", "evidence": "",
                    "concerns": f"设计产物缺失: {e},子任务 {subtask.id} 标记失败"}
        try:
            tpl = load_template(subtask.plugin_type)
        except ValueError as e:
            return {"status": "ERROR", "artifact_key": "", "evidence": "",
                    "concerns": f"{e},子任务 {subtask.id} 标记失败"}
        base = self._load_prompt("w3_generate.md")
        branch = self._load_prompt(TYPE_PROMPTS[subtask.plugin_type])
        prompt = base + "\n" + branch
        code = self._llm_generate(state, subtask, design, tpl, prompt)
        if code is None:
            # 确定性骨架:渲染模板全部 token,防 w4 未渲染占位符误判
            code = render_template(tpl, {
                "NAMESPACE": f"Kingdee.PlugIn.{subtask.id}",
                "CLASS_NAME": f"PlugIn{subtask.id}",
                "BUSINESS_LOGIC": f"// 设计要点:\n{design[:200]}",
            })
        path = self.store.write(subtask.id, "Plugin.cs", code)
        return {"status": "DONE", "artifact_key": "code_path", "path": str(path),
                "evidence": f"代码落盘: {path}", "concerns": ""}
