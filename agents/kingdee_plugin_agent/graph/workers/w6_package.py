"""w6 打包:子任务产物 → 交付包(源码+DLL+部署说明+记录)。

多子任务合并行为(终审 C9 裁决,v1 务实方案):**按子任务逐包交付** ——
每个子任务一个 zip,图上包装器把所有包路径记入 state.final_deliverables
(列表,并行打包用 reducer 追加合并),state.final_deliverable 保留最近一个
(兼容 C9 既有单测契约)。v2 再把多子任务合并为单一 zip(设计 §6.6 ⑦)。

记录接线(P2-9 修复,设计 §5.4/§12):design.md + review.json 从产物库读入
deliverable 的 design/review 键,随包落 records/design.json + records/review.json
(缺失/损坏容错为空,不阻塞打包)。review.json 含全部 findings(Critical/
Important/Minor),Minor 意见因此自动进包。
"""
import json

from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase


class PackageWorker(WorkerBase):
    name = "w6"

    def __init__(self, llm, store, builder=None, output_dir=None):
        super().__init__(llm, store)
        from pathlib import Path
        self.builder = builder  # PackageBuilder 实例,测试可注入
        self.output_dir = Path(output_dir) if output_dir else Path("data/kingdee-deliverables")

    def _read_design(self, subtask_id: str) -> dict:
        """design.md → {"content": <正文>}(markdown 原样进 records/design.json)。"""
        try:
            return {"content": self.store.read(subtask_id, "design.md")}
        except Exception:
            return {}

    def _read_review(self, subtask_id: str) -> dict | list:
        """review.json → findings 列表(含 Minor);缺失/损坏容错为空。"""
        try:
            return json.loads(self.store.read(subtask_id, "review.json"))
        except Exception:
            return {}

    def _execute(self, state, subtask) -> dict:
        from agents.kingdee_plugin_agent.tools.package import PackageBuilder
        builder = self.builder or PackageBuilder(output_dir=self.output_dir)
        # 需求版本冻结(设计 §8):spec_version + 冻结 spec 快照盖进交付记录
        # (records/spec.json),交付物可审计"这份包对应哪个版本的需求"。
        deliverable = {"code": self.store.read(subtask.id, "Plugin.cs"), "dll_path": "",
                       "subtask_id": subtask.id,  # 文件名带子任务 id,并行打包互不覆盖
                       "spec_version": state.spec_version,
                       "requirement_spec": state.requirement_spec,
                       # 记录接线(设计 §5.4/§12):设计文档 + 审查记录随包交付,
                       # PackageBuilder 写 records/design.json + records/review.json
                       "design": self._read_design(subtask.id),
                       "review": self._read_review(subtask.id)}
        path = builder.build(deliverable)
        state.final_deliverable = str(path)
        return {"status": "DONE", "artifact_key": "final_deliverable", "path": str(path),
                "evidence": f"交付包: {path}", "concerns": ""}
