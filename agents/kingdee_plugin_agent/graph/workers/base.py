"""worker 统一基类:契约/上报/状态机一次实现,子类只写 _execute。"""
from pathlib import Path

from agents.kingdee_plugin_agent.graph.state import Subtask, TaskState
from agents.kingdee_plugin_agent.store.artifact_store import ArtifactStore


class WorkerBase:
    name: str = "base"

    def __init__(self, llm, store: ArtifactStore):
        self.llm = llm
        self.store = store
        self._prompt_dir = Path(__file__).parent.parent.parent / "prompts"

    def _load_prompt(self, name: str) -> str:
        p = self._prompt_dir / name
        if not p.exists():
            raise FileNotFoundError(f"prompt 缺失: {p}")
        return p.read_text(encoding="utf-8")

    def _report(self, status: str, artifact_key: str, evidence: str, concerns: str) -> str:
        return (f"STATUS: {status}\n产物: {artifact_key}\n证据: {evidence}\n关注点: {concerns}")

    def run(self, state: TaskState, subtask: Subtask) -> tuple[Subtask, str]:
        """执行本环节,返回(更新后的 subtask, 上报消息)。"""
        result = self._execute(state, subtask)
        status = result["status"]
        key = result.get("artifact_key", "")
        if key:
            setattr(subtask, key, result.get("path", ""))
        subtask.report = {"worker": self.name, **result}
        return subtask, self._report(status, key, result.get("evidence", ""), result.get("concerns", ""))

    def _execute(self, state: TaskState, subtask: Subtask) -> dict:
        raise NotImplementedError
