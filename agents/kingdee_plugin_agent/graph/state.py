"""图 State:子任务池 + TodoList + 契约 + 指标。

子任务生命周期:
  pending → in_progress → design_done → gen_done → review_done
  → compile_done → smoke_done → packaged → delivered
  → needs_rework(退回 w3 重新生成,扣返工预算) / blocked(等用户) / failed(终态)

C10 追加的图运行字段(agent.py 主管循环使用):
  action / dispatch_id / user_feedback / metrics
  clarify_questions / clarify_answers / clarify_feedback / clarify_round
  confirm_attempts / spec_confirmed / final_deliverables(多子任务交付包合并)
  started_at / spec_version(设计 §8:时间预算 + 需求版本冻结)
"""
from dataclasses import dataclass, field
from typing import Annotated

TASK_STATUS = ("pending", "in_progress", "design_done", "gen_done", "review_done",
               "compile_done", "smoke_done", "packaged", "delivered",
               "needs_rework", "blocked", "failed")

GLOBAL_REWORK_BUDGET = 3   # 全局返工预算:总重新生成 ≤3 轮
MAX_PARALLEL = 3           # send() 并行子任务上限
PIPELINE_TIME_BUDGET = 1800.0  # 全流程时间预算(秒):图级总闸(设计 §8 时间预算超限)

#: 任务指标计数键(设计 §9/§12:pass-rate / 返工轮次 / 冒烟通过率随 State 统计)。
#: 口径:w5/w5_5 每次执行结果计数(重工重跑会再计,= 编译/冒烟轮次结果);
#: rework_rounds 由主管按返工事件累计(覆盖 w4 重审 + w5 超限 + w5_5 冒烟失败,
#: 与返工预算扣减同源,预算扣 1 = 返工 1 轮)。
METRIC_KEYS = ("compile_pass_count", "compile_fail_count", "rework_rounds",
               "smoke_pass_count", "smoke_fail_count")


def _merge_metrics(current: dict, update: dict) -> dict:
    """metrics 通道 reducer:按 key 求和合并(并行分支各自上报增量)。

    分支 worker 只上报本步增量(delta,执行前后差值),不是全量 —— 全量
    求和会在跨多轮派发时重复累计(旧值被反复加回);主管 rework_rounds 同
    理只上报本次事件数。缺键补齐 0:图级通道初始化不给 dataclass 默认值
    (实测 Annotated 通道初始为空 dict),补齐后计数键始终完整。
    """
    merged = {k: current.get(k, 0) + update.get(k, 0)
              for k in set(current) | set(update)}
    for k in METRIC_KEYS:
        merged.setdefault(k, 0)
    return merged


@dataclass
class Subtask:
    id: str
    plugin_type: str          # bill | service | list
    title: str
    deps: list[str] = field(default_factory=list)
    status: str = "pending"
    # ── 下发模板字段(设计 §5.1:验收标准 / 上限,w1 拆解时按确认规格填写)──
    acceptance_criteria: str = ""   # 该环节可验证的完成标准(w4 审查对照用;空 = 按需求确认摘要验收)
    max_rework: int = 0             # 本子任务退回上限,0 = 全局默认 GLOBAL_REWORK_BUDGET
    rework_count: int = 0           # 本子任务已发生的返工轮次(主管统一维护,见 agent._advance_status)
    design_path: str = ""
    code_path: str = ""
    dll_path: str = ""            # 编译产物 DLL 路径(w5 成功时取自编译后端;mock 后端无产出为空)
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
    # ── 任务指标(设计 §9/§12):计数通道,reducer 求和合并增量 ──
    metrics: Annotated[dict, _merge_metrics] = field(
        default_factory=lambda: {k: 0 for k in METRIC_KEYS})
    # ── w1 澄清状态机 ──
    clarify_questions: list[str] = field(default_factory=list)
    clarify_answers: list[str] = field(default_factory=list)
    clarify_feedback: list[str] = field(default_factory=list)   # 确认未通过时的补充
    clarify_round: int = 0
    confirm_attempts: int = 0
    spec_confirmed: bool = False
