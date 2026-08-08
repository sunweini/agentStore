"""主管节点:派发/编排/返工预算/并发上限。

决策循环:
  主管 ──► 摘要表注入 ──► LLM 选动作 ──► 执行/问用户/收尾
  派发前检查:依赖满足 + 并发 ≤3 + 返工预算
"""
from agents.kingdee_plugin_agent.graph.state import TaskState, Subtask, MAX_PARALLEL
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase


class Supervisor:
    """子任务调度主管。

    - _next_ready:依赖满足 + 并发 < MAX_PARALLEL 时返回下一个可派发的 pending 子任务
    - _check_budget:返工预算是否还有余额
    - _summary_table:把子任务池渲染成 LLM 可读的摘要表
    - decide:决策入口(确定性子集;LLM 选动作在 C10 agent.py 接线)
    """

    def __init__(self, llm, workers: dict[str, WorkerBase]):
        self.llm = llm
        self.workers = workers

    def _summary_table(self, state: TaskState) -> str:
        lines = [f"返工预算剩余: {state.rework_budget_left}"]
        for s in state.todo:
            lines.append(f"  {s.id} [{s.plugin_type}] {s.status} deps={s.deps} 产物: {s.design_path or s.code_path}")
        return "\n".join(lines)

    def _next_ready(self, state: TaskState) -> Subtask | None:
        """返回第一个可派发的 pending 子任务,无则 None。

        依赖满足:每个 dep 对应子任务不存在(视为可选依赖)或其状态为
        packaged/delivered。并发检查:in_progress 数 < MAX_PARALLEL。
        """
        running = [s for s in state.todo if s.status == "in_progress"]
        if len(running) >= MAX_PARALLEL:
            return None
        status_by_id = {t.id: t.status for t in state.todo}
        for s in state.todo:
            if s.status != "pending":
                continue
            if all(status_by_id.get(d) in (None, "packaged", "delivered") for d in s.deps):
                return s
        return None

    def _check_budget(self, state: TaskState) -> bool:
        return state.rework_budget_left > 0

    def decide(self, state: TaskState) -> str:
        """返回动作: run:<subtask_id> | ask_user | finish | fail

        当前为确定性子集:有可派发子任务 → run,否则 ask_user。
        LLM 基于摘要表选动作(run:<worker>:<subtask_id> | ask_user | finish | fail)
        在 C10 agent.py 接线,此处保留骨架。
        """
        ready = self._next_ready(state)
        if ready:
            return f"run:{ready.id}"
        # 真实实现:LLM 基于摘要表选择(此处给确定性子集;LLM 决策在 agent.py 接线)
        return "ask_user"
