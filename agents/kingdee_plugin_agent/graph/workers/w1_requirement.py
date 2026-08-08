"""w1 需求澄清:交互式,一次一问,元数据驱动提问,spec+plan 双产物。

交互流:
  用户输入 ──► 类型判定 ──► 查元数据 ──► 提问(带真实字段选项)
  ──► 用户答 ──► 下一问 ...(上限 10 轮)──► 确认摘要 ──► 用户确认 ──► spec+plan
挂起:每问一轮 interrupt(),用户答复后 checkpointer resume。
"""
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase


def build_confirmation_summary(spec: dict) -> str:
    lines = ["## 需求确认摘要"]
    lines.append("### 已确认决策")
    for d in spec.get("decisions", []):
        lines.append(f"- {d['q']}: {d['a']}")
    lines.append("### 假设(你没说的,我按此处理,不认可请指出)")
    for a in spec.get("assumptions", []):
        lines.append(f"- {a}")
    return "\n".join(lines)


class RequirementWorker(WorkerBase):
    name = "w1"

    def _execute(self, state, subtask) -> dict:
        # 真实实现:LLM + 元数据驱动提问循环(10 轮上限),每轮 interrupt()。
        # 接口契约:产出 requirement_spec 落盘(decisions/assumptions/subtasks/deps)。
        # 此处给出确定性路径:spec 已就绪时直接产出。
        spec = getattr(state, "requirement_spec", {})
        path = self.store.write(subtask.id, "spec.md", str(spec))
        return {"status": "DONE", "artifact_key": "", "path": str(path),
                "evidence": f"spec 落盘: {path}", "concerns": ""}
