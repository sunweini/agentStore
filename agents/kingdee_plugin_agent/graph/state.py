"""图 State:子任务池 + TodoList + 契约 + 指标。

子任务生命周期:
  pending → in_progress → design_done → gen_done → review_done
  → compile_done → smoke_done → packaged → delivered
  → blocked(等用户) / failed(达上限)
"""
from dataclasses import dataclass, field

TASK_STATUS = ("pending", "in_progress", "design_done", "gen_done", "review_done",
               "compile_done", "smoke_done", "packaged", "delivered", "blocked", "failed")

GLOBAL_REWORK_BUDGET = 3   # 全局返工预算:总重新生成 ≤3 轮
MAX_PARALLEL = 3           # send() 并行子任务上限


@dataclass
class Subtask:
    id: str
    plugin_type: str          # bill | service | list
    title: str
    deps: list[str] = field(default_factory=list)
    status: str = "pending"
    design_path: str = ""
    code_path: str = ""
    compile_errors: list[dict] = field(default_factory=list)
    review_verdict: str = ""  # Approved | Needs fixes
    report: dict = field(default_factory=dict)


@dataclass
class TaskState:
    requirement_spec: dict
    todo: list[Subtask]
    rework_budget_left: int = GLOBAL_REWORK_BUDGET
    final_deliverable: str = ""
    environment: dict = field(default_factory=dict)
