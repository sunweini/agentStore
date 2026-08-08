"""w3 代码生成:模板骨架 + 类型分支 + RAG 指南参数化。模板优先,冲突以模板为准。

骨架路径:读 design.md → 载入类型模板(templates/<type>/template.cs)→
渲染 {{BUSINESS_LOGIC}}(设计要点注入,其余占位符留待真实 LLM 实现)→ 落盘 Plugin.cs。
"""
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase
from agents.kingdee_plugin_agent.templates import load_template, render_template

TYPE_PROMPTS = {"bill": "w3_generate_bill.md", "service": "w3_generate_service.md", "list": "w3_generate_list.md"}


class GenerateWorker(WorkerBase):
    name = "w3"

    def __init__(self, llm, store, rag=None):
        super().__init__(llm, store)
        self.rag = rag

    def _execute(self, state, subtask) -> dict:
        design = self.store.read(subtask.id, "design.md")
        tpl = load_template(subtask.plugin_type)
        base = self._load_prompt("w3_generate.md")
        branch = self._load_prompt(TYPE_PROMPTS[subtask.plugin_type])
        # 真实实现:LLM 输入 design + 模板(tpl) + 指南检索(rag.guide,类型过滤)
        #   → 按 base/branch 要点渲染全部 {{TOKEN}},模板优先、冲突以模板为准。
        code = render_template(tpl, {"BUSINESS_LOGIC": f"// 设计:\n{design[:200]}"})
        path = self.store.write(subtask.id, "Plugin.cs", code)
        return {"status": "DONE", "artifact_key": "code_path", "path": str(path),
                "evidence": f"代码落盘: {path}", "concerns": ""}
