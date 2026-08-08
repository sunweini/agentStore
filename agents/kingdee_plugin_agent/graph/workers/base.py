"""worker 统一基类:契约/上报/状态机一次实现,子类只写 _execute。"""
from pathlib import Path

from agents.kingdee_plugin_agent.graph.state import Subtask, TaskState
from agents.kingdee_plugin_agent.store.artifact_store import ArtifactStore
from common.otel import get_tracer


class WorkerBase:
    name: str = "base"

    def __init__(self, llm, store: ArtifactStore):
        self.llm = llm
        self.store = store
        self._prompt_dir = Path(__file__).parent.parent.parent / "prompts"

    def _load_prompt(self, name: str) -> str:
        # 带 "/" 的名字按 skill 路径解析(如 design-builder/references/bill.md,
        # 相对 skills/ 根 —— 类型分支方法论单源在 skill references,见 skills/loader.py)
        if "/" in name:
            p = self._prompt_dir.parent / "skills" / name
        else:
            p = self._prompt_dir / name
        if not p.exists():
            raise FileNotFoundError(f"prompt 缺失: {p}")
        return p.read_text(encoding="utf-8")

    def _report(self, status: str, artifact_key: str, evidence: str, concerns: str) -> str:
        """上报消息。状态契约(设计):DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT。

        NEEDS_CONTEXT 为设计契约保留值,当前无产出路径(无 worker 上报,图侧
        亦无分支处理)—— 若未来 worker 需要"缺信息"态,先接通 supervisor 处理
        再加,勿裸用。
        """
        return (f"STATUS: {status}\n产物: {artifact_key}\n证据: {evidence}\n关注点: {concerns}")

    def run(self, state: TaskState, subtask: Subtask) -> tuple[Subtask, str]:
        """执行本环节,返回(更新后的 subtask, 上报消息)。

        可观测(设计 §12):每个 worker 执行打一个 span(worker 状态变迁可观测),
        低基数属性(subtask_id/plugin_type/status),无用户信息(遵循 OBS-CORE-003)。
        """
        with get_tracer().start_as_current_span(f"kingdee.worker.{self.name}") as span:
            span.set_attribute("subtask_id", subtask.id)
            span.set_attribute("plugin_type", subtask.plugin_type)
            result = self._execute(state, subtask)
            span.set_attribute("status", result["status"])
        status = result["status"]
        key = result.get("artifact_key", "")
        if key:
            setattr(subtask, key, result.get("path", ""))
        subtask.report = {"worker": self.name, **result}
        return subtask, self._report(status, key, result.get("evidence", ""), result.get("concerns", ""))

    def _execute(self, state: TaskState, subtask: Subtask) -> dict:
        raise NotImplementedError
