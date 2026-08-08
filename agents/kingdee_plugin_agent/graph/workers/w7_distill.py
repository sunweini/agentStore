"""w7 知识沉淀:踩坑/编译错误 → 经验库 proposed 态;失败不阻塞交付。"""
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase


class DistillWorker(WorkerBase):
    name = "w7"

    def __init__(self, llm, store, experience=None):
        super().__init__(llm, store)
        self.experience = experience

    def _execute(self, state, subtask) -> dict:
        try:
            for err in subtask.compile_errors:
                if self.experience:
                    self.experience.propose(err["code"], "", err["message"], "w7 沉淀,待人工验证")
            return {"status": "DONE", "artifact_key": "", "evidence": "沉淀完成", "concerns": ""}
        except Exception as e:  # 沉淀失败不阻塞交付
            return {"status": "DONE_WITH_CONCERNS", "artifact_key": "", "evidence": "",
                    "concerns": f"沉淀失败: {e},记待沉淀队列"}
