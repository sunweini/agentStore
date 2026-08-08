"""w2 设计:类型分支配置表驱动,设计文档落盘。"""
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase

TYPE_PROMPTS = {"bill": "w2_design_bill.md", "service": "w2_design_service.md", "list": "w2_design_list.md"}


class DesignWorker(WorkerBase):
    name = "w2"

    def __init__(self, llm, store, rag=None):
        super().__init__(llm, store)
        self.rag = rag

    def _execute(self, state, subtask) -> dict:
        base = self._load_prompt("w2_design.md")
        branch = self._load_prompt(TYPE_PROMPTS[subtask.plugin_type])
        prompt = base + "\n" + branch
        # 真实实现:LLM + RAG 检索(api_ref+guide,类型过滤)生成设计文档
        design = f"# 设计:{subtask.title}\n类型:{subtask.plugin_type}\n{prompt}"  # 占位 → 执行时替换为 LLM 产物
        path = self.store.write(subtask.id, "design.md", design)
        return {"status": "DONE", "artifact_key": "design_path", "path": str(path),
                "evidence": f"设计落盘: {path}", "concerns": ""}
