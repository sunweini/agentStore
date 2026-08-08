"""w6 打包:子任务产物合并 → 交付包(源码+DLL+部署说明+记录)。"""
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase


class PackageWorker(WorkerBase):
    name = "w6"

    def __init__(self, llm, store, builder=None, output_dir=None):
        super().__init__(llm, store)
        from pathlib import Path
        self.builder = builder  # PackageBuilder 实例,测试可注入
        self.output_dir = Path(output_dir) if output_dir else Path("data/kingdee-deliverables")

    def _execute(self, state, subtask) -> dict:
        from agents.kingdee_plugin_agent.tools.package import PackageBuilder
        builder = self.builder or PackageBuilder(output_dir=self.output_dir)
        deliverable = {"code": self.store.read(subtask.id, "Plugin.cs"), "dll_path": ""}
        path = builder.build(deliverable)
        state.final_deliverable = str(path)
        return {"status": "DONE", "artifact_key": "final_deliverable", "path": str(path),
                "evidence": f"交付包: {path}", "concerns": ""}
