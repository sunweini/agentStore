"""图 State:子任务池 + TodoList + 契约 + 指标。

子任务生命周期:
  pending → in_progress → design_done → gen_done → review_done
  → compile_done → smoke_done → packaged → delivered
  → needs_rework(退回 w3 重新生成,扣返工预算) / blocked(等用户) / failed(终态)

C10 追加的图运行字段(agent.py 主管循环使用):
  action / dispatch_id / user_feedback / ask_question
  clarify_questions / clarify_answers / clarify_feedback / clarify_round
  confirm_attempts / spec_confirmed / final_deliverables(多子任务交付包合并)
"""
from dataclasses import dataclass, field
from typing import Annotated

TASK_STATUS = ("pending", "in_progress", "design_done", "gen_done", "review_done",
               "compile_done", "smoke_done", "packaged", "delivered",
               "needs_rework", "blocked", "failed")

GLOBAL_REWORK_BUDGET = 3   # 全局返工预算:总重新生成 ≤3 轮
MAX_PARALLEL = 3           # send() 并行子任务上限
PIPELINE_TIME_BUDGET = 1800.0  # 全流程时间预算(秒):图级总闸(设计 §8 时间预算超限)


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
    review_path: str = ""     # w4 审查报告路径(artifact_key 契约,见 C7 修复)
    report: dict = field(default_factory=dict)


def _merge_todo(current: list[Subtask], update: list[Subtask]) -> list[Subtask]:
    """todo 通道 reducer:按子任务 id 合并(并行 Send 分支各自回写自己的子任务)。

    C10 实测(LangGraph 1.2.10):send() 并行分支的返回经 reducer 逐条合并,
    无 reducer 时多分支写同一通道会互相覆盖丢数据。
    """
    by_id = {s.id: s for s in current}
    for s in update:
        by_id[s.id] = s
    return list(by_id.values())


def _merge_deliverables(current: list[str], update: list[str]) -> list[str]:
    """final_deliverables 通道 reducer:追加合并(两个子任务并行打包时互不覆盖)。"""
    return list(dict.fromkeys(list(current) + list(update)))


def _last_wins(current, update):
    """标量通道 reducer:多分支同一步写同一通道时取最后写入(并行分支必需)。

    实测(1.2.10):并行 Send 分支同一步写 last-value 通道直接抛
    InvalidUpdateError;加 reducer 后 last-wins 合并。
    """
    return update


def _merge_events(current: list[int], update: list[int]) -> list[int]:
    """rework_events 通道 reducer:替换合并(分支报 [1],主管决策后写 [] 清空)。

    返工预算由主管统一扣减(rework_budget_left 保持普通字段,dataclass 默认
    值 3 才生效 —— 实测 Annotated 通道初始化用类型默认值,int → 0,会把
    预算默认冲成 0)。分支在各自超步写 [1]、主管在下一超步应用并写 [] 清空,
    写方天然不同步冲突;同一步内两个并行分支同时报事件(罕见)last-wins
    合并丢一次 —— v1 接受该近似(并行返工属边缘场景,见 C10 报告)。
    """
    return list(update)


@dataclass
class TaskState:
    requirement_spec: dict
    todo: Annotated[list[Subtask], _merge_todo] = field(default_factory=list)
    rework_budget_left: int = GLOBAL_REWORK_BUDGET   # 普通字段:主管唯一写者(默认值 3 生效)
    rework_events: Annotated[list[int], _merge_events] = field(default_factory=list)
    final_deliverable: Annotated[str, _last_wins] = ""   # 最近一个交付包(兼容 C9 既有契约)
    final_deliverables: Annotated[list[str], _merge_deliverables] = field(default_factory=list)
    environment: dict = field(default_factory=dict)
    # ── 时间预算(设计 §8)──
    # started_at: 建任务时间戳(time.time());0.0 = 未设置(旧状态兼容,不做预算判定)。
    # 存于 state 而非 thread_id:挂起 resume 后 checkpointer 恢复同一份值,不重置。
    # spec_version: 需求版本号,spec 确认时置 1;确认后冻结不可变,修改需求须开新任务。
    started_at: float = 0.0
    spec_version: int = 1
    # ── 主管循环(agent.py)──
    action: str = ""                     # run:<sid> | ask_user[:<问题>] | finish | fail[:<原因>]
    dispatch_id: str = ""                # Send 分支输入通道(分支不写回,并行写会冲突)
    user_feedback: list[str] = field(default_factory=list)
    # ── w1 澄清状态机 ──
    clarify_questions: list[str] = field(default_factory=list)
    clarify_answers: list[str] = field(default_factory=list)
    clarify_feedback: list[str] = field(default_factory=list)   # 确认未通过时的补充
    clarify_round: int = 0
    confirm_attempts: int = 0
    spec_confirmed: bool = False
