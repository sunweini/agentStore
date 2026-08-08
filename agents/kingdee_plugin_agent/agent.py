"""kingdee-plugin-agent 图构建:主管循环 + 8 worker + interrupt/send/recursion_limit。

图结构:
  START → supervisor →(route: run:→dispatcher / ask_user→w1 / finish|fail→END)
  dispatcher ──Command(goto=[Send(worker, payload)...])──► worker 节点 ×N(并行 ≤3)
  每个 worker / w1 → supervisor(回到主管循环)…… 直至 finish/fail

LangGraph 1.2.10 API 用法(经安装包 introspection 实测核对,本文件即依据,
详见 .superpowers/sdd/2026-08-08-kingdee-plugin-agent-plan-c-orchestration/task-C10-report.md):
  - 状态 schema 用 dataclass TaskState:节点入参为 schema 构造的实例
  - 并行派发:节点返回 Command(update=..., goto=[Send(node, payload), ...]);
    **Send 分支入参 = payload 快照**(不是全量 state),所以 payload 必须携带
    worker 需要的通道(todo/rework_budget_left/environment/requirement_spec 等)
  - 分支结果按通道 reducer 合并(todo 按 id,见 state._merge_todo)——无 reducer 会互相覆盖
  - 用户交互:interrupt(value) 挂起 → 结果含 __interrupt__;Command(resume=answer) 恢复
  - recursion_limit 是运行时 config 参数(graph.invoke(..., config={"recursion_limit": N})),
    不是 compile 参数(compile 签名已核对);按子任务数预算,见 default_recursion_limit
  - checkpointer 缺省 MemorySaver(interrupt 必需);生产可换 AsyncSqliteSaver
"""
from __future__ import annotations

import json
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from agents.kingdee_plugin_agent.graph.state import METRIC_KEYS, Subtask, TaskState
from agents.kingdee_plugin_agent.graph.supervisor import Supervisor, worker_for_subtask
from agents.kingdee_plugin_agent.graph.workers.w1_requirement import (
    RequirementWorker,
    build_confirmation_summary,
    is_confirmed,
)
from agents.kingdee_plugin_agent.graph.workers.w2_design import DesignWorker
from agents.kingdee_plugin_agent.graph.workers.w3_generate import GenerateWorker
from agents.kingdee_plugin_agent.graph.workers.w4_review import ReviewWorker
from agents.kingdee_plugin_agent.graph.workers.w5_5_smoke import SmokeWorker
from agents.kingdee_plugin_agent.graph.workers.w5_compile import CompileWorker
from agents.kingdee_plugin_agent.graph.workers.w6_package import PackageWorker
from agents.kingdee_plugin_agent.graph.workers.w7_distill import DistillWorker
from agents.kingdee_plugin_agent.store.artifact_store import ArtifactStore
from agents.kingdee_plugin_agent.tools.compile_client import compile_client_from_env
from agents.kingdee_plugin_agent.tools.kingdee_api import KingdeeApiClient
from agents.kingdee_plugin_agent.tools.smoke_client import SmokeClient
from common.llm import get_chat_model

AGENT_NAME = "kingdee_plugin_agent"

_TASKSTATE_FIELDS = frozenset(TaskState.__dataclass_fields__)
_SUBTASK_FIELDS = frozenset(Subtask.__dataclass_fields__)

_UNSET = object()


def default_recursion_limit(todo_count: int) -> int:
    """按子任务数预算 recursion_limit(设计 §6.2):100 + 20 × 子任务数。

    recursion_limit 是运行时 config 参数(graph.invoke(..., config=...)),
    不是 compile 参数 —— 调用方按任务规模显式传入,默认给足并设上限。
    预算依据(终审复核):8 阶段流水线 × 每阶段往返 + 返工重跑,复合任务
    (7 子任务)实测需 ~120 超步;旧 50+10×n 在 n=10 时 150 < 实际 ~160 →
    GraphRecursionError,返工即溢出;100+20×n 留舒适余量(n=10 → 300,
    CLI/API 调用点澄清期按上限 10 给足)。
    """
    return 100 + 20 * todo_count


def _as_state(payload) -> TaskState:
    """Send 分支入参(payload 快照 dict)→ TaskState;常规节点入参(实例)原样。

    Send 分支入参实测为 payload dict(见模块 docstring),worker 代码按
    TaskState 属性访问,这里统一转换;todo 条目兼容 Subtask 实例 / dict(JSON 反序列化)。
    metrics 缺键补齐 0:图级通道初始化给空 dict(实测 Annotated 通道不取
    dataclass 默认值),补齐后 worker 的计数 `+=` 不会 KeyError。
    """
    if isinstance(payload, TaskState):
        return payload
    data = {k: payload[k] for k in _TASKSTATE_FIELDS if k in payload}
    todo = []
    for s in data.get("todo", []):
        if isinstance(s, Subtask):
            todo.append(s)
        else:
            todo.append(Subtask(**{k: s[k] for k in _SUBTASK_FIELDS if k in s}))
    data["todo"] = todo
    st = TaskState(**data)
    for k in METRIC_KEYS:
        st.metrics.setdefault(k, 0)
    return st


def _find_subtask(state: TaskState, dispatch_id: str) -> Subtask:
    for s in state.todo:
        if s.id == dispatch_id:
            return s
    raise ValueError(f"派发目标子任务不存在: {dispatch_id}")


def _send_payload(state: TaskState, subtask: Subtask) -> dict:
    """Send 分支 payload:worker 依赖的通道快照 + 派发目标 id。"""
    return {
        "todo": state.todo,
        "dispatch_id": subtask.id,
        "rework_budget_left": state.rework_budget_left,
        "environment": state.environment,
        "requirement_spec": state.requirement_spec,
        "spec_version": state.spec_version,   # w6 打包把冻结版本盖进交付记录
        "final_deliverable": state.final_deliverable,
        "final_deliverables": state.final_deliverables,
        # 指标计数快照(w5/w5_5 增量上报):dict() 拷贝 —— 并行分支若共享同一
        # dict 引用,worker 的 `+=` 会原地改通道当前值,reducer 在此基础上再
        # 求和导致重复累计(实测并行双任务 compile_pass_count=4 而非 2)
        "metrics": dict(state.metrics),
    }


def build_graph(store=None, compile_client=None, rag=None, standards=None,
                api_client=None, llm=_UNSET, smoke_client=None, experience=None,
                package_builder=None, output_dir=None, checkpointer=None):
    """构建 kingdee-plugin-agent 主管图。所有依赖可注入(测试传 fake),缺省生产默认。

    Args:
        store: 产物落盘(缺省 ArtifactStore())
        compile_client: 编译客户端(缺省从 COMPILE_SERVICE_URL 构造)
        rag: RAG 客户端(w2/w3 检索;None = 无检索降级)
        standards: 规范库加载器(w4 审查;None = 无规范注入)
        api_client: 金蝶元数据客户端(冒烟验证用;缺省从 KD_* 环境变量构造)
        llm: 聊天模型(缺省 get_chat_model();显式传 None = 确定性骨架路径)
        smoke_client: 冒烟客户端(缺省基于 api_client/环境构造,无环境则 None)
        experience: 经验库(w2 设计历史坑参考 / w5 修复检索 / w7 沉淀;None = 跳过)
        package_builder/output_dir: 交付包构建(缺省 PackageBuilder(output_dir))
        checkpointer: 缺省 MemorySaver(interrupt 必需);生产可换 AsyncSqliteSaver

    Returns:
        CompiledStateGraph(langgraph.json 注册入口 build_graph)
    """
    llm = get_chat_model() if llm is _UNSET else llm
    store = store or ArtifactStore()
    compile_client = compile_client or compile_client_from_env()
    api = api_client or KingdeeApiClient.client_from_env_or_none()
    smoke_client = smoke_client or (SmokeClient(api) if api else None)

    workers = {
        "w1": RequirementWorker(llm=llm, store=store),
        "w2": DesignWorker(llm=llm, store=store, rag=rag, experience=experience),
        "w3": GenerateWorker(llm=llm, store=store, rag=rag),
        "w4": ReviewWorker(llm=llm, store=store, rag=rag, standards=standards),
        "w5": CompileWorker(llm=llm, store=store, compile_client=compile_client,
                            experience=experience),
        "w5_5": SmokeWorker(llm=llm, store=store, smoke_client=smoke_client),
        "w6": PackageWorker(llm=llm, store=store, builder=package_builder,
                            output_dir=output_dir),
        "w7": DistillWorker(llm=llm, store=store, experience=experience),
    }
    supervisor = Supervisor(llm=llm, workers=workers)

    # ── 节点 ────────────────────────────────────────────────────────

    def supervisor_node(state: TaskState) -> dict:
        """主管:先应用分支上报的返工事件(并行返工精确累计),再决策;
        decide 可能原地标记 todo(级联失败/预算耗尽),整体回写。
        预算是主管唯一写者(rework_budget_left 普通字段,默认 3 才生效)。
        指标:返工事件数 → rework_rounds 增量(与预算扣减同源,预算扣 1 = 返工 1 轮)。"""
        n_events = sum(state.rework_events)
        if n_events:
            state.rework_budget_left = max(0, state.rework_budget_left - n_events)
        action = supervisor.decide(state)
        updates = {"action": action, "todo": state.todo,
                   "rework_budget_left": state.rework_budget_left,
                   "rework_events": []}
        if n_events:
            updates["metrics"] = {"rework_rounds": n_events}
        return updates

    def route(state: TaskState):
        """主管动作 → 节点/END 的机械映射(终态判定在 Supervisor.decide)。

        fail → w6_fail 失败打包节点(先交"未完成"包再 END,设计 §8 失败收尾);
        finish 直接 END。
        """
        a = state.action or ""
        if a.startswith("run:"):
            return "dispatcher"
        if a.startswith("ask_user"):
            return "w1"
        if a.startswith("fail"):
            return "w6_fail"
        return END  # finish

    def dispatcher_node(state: TaskState):
        """批量派发:依赖满足的 pending 子任务 send() 并行(并发 ≤ MAX_PARALLEL)。

        Command(update=..., goto=[Send(...)]):update 先把批内子任务标 in_progress
        (并发计数),分支各自跑 worker 后按 id reducer 合并回写(实测核对)。
        """
        sid = state.action.split(":", 1)[1] if ":" in state.action else ""
        batch = supervisor._ready_batch(state, prefer=sid)
        updates, sends = [], []
        for s in batch:
            worker = worker_for_subtask(s)
            if worker is None or worker == "w1":
                continue  # 终态/blocked 不派发:w1 是交互节点,不走 Send 分支
            s.status = "in_progress"
            updates.append(s)
            sends.append(Send(worker, _send_payload(state, s)))
        if not sends:
            return Command(goto="supervisor")  # 防御:状态漂移无派发 → 回主管重决策
        return Command(update={"todo": updates, "action": ""}, goto=sends)

    def fail_package_node(state: TaskState) -> dict:
        """失败收尾(设计 §8):收集部分产物 + 全部退回意见 + 原因 → "未完成"包。

        仅在终态 fail 时经 route 进入(w6_fail 节点):从产物库收集每个未交付
        子任务已有产物(design.md / Plugin.cs / review.json,缺失容忍)+
        compile_errors(编译超限 5 轮后的错误日志,已记在 subtask)+ 审查裁决,
        PackageBuilder.build_failed 打成 `deliverable-failed-<ts>.zip`,
        记入 final_deliverable(s) —— CLI/API 与正常交付包同一通道展示,
        失败也有可审计产物(原实现 fail 只有 TodoList 摘要)。
        """
        from agents.kingdee_plugin_agent.tools.package import PackageBuilder
        builder = package_builder or PackageBuilder(output_dir=output_dir or Path("data/kingdee-deliverables"))
        collected = []
        for s in state.todo:
            if s.status == "delivered":
                continue  # 已交付子任务不进未完成包
            entry = {"id": s.id, "status": s.status,
                     "review_verdict": s.review_verdict,
                     "compile_errors": list(s.compile_errors)}
            for name, key in (("design.md", "design"), ("Plugin.cs", "code")):
                try:
                    entry[key] = store.read(s.id, name)
                except Exception:
                    pass  # 未走到该阶段 → 产物缺失跳过
            try:
                review = store.read(s.id, "review.json")
                entry["review"] = json.loads(review)
            except Exception:
                pass
            collected.append(entry)
        path = builder.build_failed(collected, reason=state.action,
                                    spec_version=state.spec_version,
                                    requirement_spec=state.requirement_spec)
        return {"final_deliverable": str(path),
                "final_deliverables": [str(path)]}  # reducer 追加合并

    def w1_node(state: TaskState) -> dict:
        """w1 交互节点:初始澄清(问题/确认,interrupt 挂起)或中途 ask_user。

        挂起语义(实测):interrupt() 所在节点 resume 时整体重跑,payload 必须由
        state 确定性得出 —— 问题清单/确认摘要均在上一轮存进 state,不依赖 LLM 重算。
        """
        if state.spec_confirmed:
            if state.action.startswith("ask_user"):
                q = state.action.split(":", 1)[1] if ":" in state.action else "请补充说明"
                answer = interrupt({"type": "ask_user", "question": q})
                return {"action": "", "user_feedback": state.user_feedback + [str(answer)]}
            return {"action": ""}  # 防御:已确认且非 ask_user,不应到达
        # —— 初始澄清:首轮先生成问题清单(不 interrupt,resume 才可确定性取题)——
        if not state.clarify_questions:
            return {"clarify_questions": workers["w1"].generate_questions(state), "action": ""}
        if state.clarify_round < len(state.clarify_questions) \
                and state.clarify_round < workers["w1"].MAX_ROUNDS:
            q = state.clarify_questions[state.clarify_round]
            answer = interrupt({"type": "question", "round": state.clarify_round, "text": q})
            workers["w1"].record_answer(state, answer)
            return {"clarify_answers": state.clarify_answers,
                    "clarify_round": state.clarify_round + 1, "action": ""}
        # —— 问题问完 → 确认摘要(决策 + 假设)——
        spec = workers["w1"].build_spec(state)
        answer = interrupt({"type": "confirm",
                            "summary": build_confirmation_summary(spec)})
        if is_confirmed(answer):
            return _confirm_and_split(state, spec)
        # 未确认:补充记入假设,最多再确认 1 次,仍不确认则带假设强制收口(防无限循环)
        state.clarify_feedback.append(str(answer))
        attempts = state.confirm_attempts + 1
        if attempts >= 2:
            spec = workers["w1"].build_spec(state)
            return {**_confirm_and_split(state, spec), "confirm_attempts": attempts}
        return {"confirm_attempts": attempts, "action": ""}

    def _confirm_and_split(state: TaskState, spec: dict) -> dict:
        """确认通过:拆子任务(LLM plan)→ spec.json/plan.json 落盘 → 交回主管派发。

        需求版本冻结(设计 §8):确认即冻结 —— 此处给 spec_version 盖章,此后
        requirement_spec 不再被任何节点修改;要改需求须开新任务。
        """
        todo = workers["w1"].split_subtasks(state, spec)
        workers["w1"].persist(spec, todo)
        return {"requirement_spec": spec, "todo": todo, "spec_confirmed": True,
                "spec_version": 1,
                "clarify_answers": [], "clarify_feedback": [],
                "clarify_round": state.clarify_round + 1, "action": ""}

    def _advance_status(name: str, sub: Subtask, st: TaskState, budget_deducted: bool) -> bool:
        """worker 报告状态 → 子任务生命周期推进(与 supervisor.STATUS_TO_WORKER 对应)。

        ERROR(未知类型/产物缺失)→ failed;w4 Needs fixes / w5 编译超限 /
        w5_5 冒烟失败 → needs_rework(退回 w3);未扣预算的 BLOCKED(基础设施缺失)
        → failed(重工无意义,防无限重试循环)。返回本次是否产生返工事件
        (w4 重审;w5/w5_5 由 worker 原地扣减经 budget_deducted 检测),由主管统一扣预算。
        """
        report_status = sub.report.get("status", "")
        if name == "w2":
            sub.status = "failed" if report_status == "ERROR" else "design_done"
        elif name == "w3":
            sub.status = "failed" if report_status == "ERROR" else "gen_done"
        elif name == "w4":
            if report_status == "ERROR":
                sub.status = "failed"
            elif sub.review_verdict == "Needs fixes":
                sub.status = "needs_rework"
                return True  # 返工事件:重新生成轮次,主管统一扣预算
            else:
                sub.status = "review_done"
        elif name == "w5":
            sub.status = "needs_rework" if (report_status == "BLOCKED" and budget_deducted) \
                else ("failed" if report_status == "BLOCKED" else "compile_done")
        elif name == "w5_5":
            sub.status = "needs_rework" if (report_status == "BLOCKED" and budget_deducted) \
                else ("failed" if report_status == "BLOCKED" else "smoke_done")
        elif name == "w6":
            sub.status = "packaged"
        elif name == "w7":
            sub.status = "delivered"
        return False

    def make_worker_node(name: str):
        """worker 分支节点:payload 快照 → TaskState → 跑 worker → 生命周期推进回写。"""

        def node(payload):
            st = _as_state(payload)
            sub = _find_subtask(st, st.dispatch_id)
            before = st.rework_budget_left
            metrics_before = dict(st.metrics)   # 指标增量 = 执行前后差值(分支只报增量)
            sub, msg = workers[name].run(st, sub)
            rework = _advance_status(name, sub, st,
                                     budget_deducted=st.rework_budget_left < before)
            updates = {"todo": [sub]}  # dispatch_id 是分支输入通道,不写回(并行写会冲突)
            if rework or st.rework_budget_left < before:
                # 返工事件上报主管统一扣预算(分支不直写预算:并行分支同一步
                # 写同一普通通道会 InvalidUpdateError,且并行累计会丢失)
                updates["rework_events"] = [1]
            delta = {k: st.metrics[k] - metrics_before.get(k, 0)
                     for k in st.metrics if st.metrics[k] != metrics_before.get(k, 0)}
            if delta:
                updates["metrics"] = delta      # 指标增量上报(reducer 求和,并行不丢)
            if name == "w6":
                path = sub.report.get("path", "")
                if path:
                    # 多子任务交付包合并(v1 逐包):追加合并,并行打包互不覆盖
                    updates["final_deliverables"] = [path]
                    updates["final_deliverable"] = path
            return updates

        return node

    # ── 图装配 ──────────────────────────────────────────────────────

    graph = StateGraph(TaskState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("dispatcher", dispatcher_node)
    graph.add_node("w1", w1_node)          # 交互节点(单独注册,非分支 worker)
    graph.add_node("w6_fail", fail_package_node)  # 失败收尾:先交未完成包再 END
    for name in workers:
        if name != "w1":
            graph.add_node(name, make_worker_node(name))

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor", route,
        {"dispatcher": "dispatcher", "w1": "w1", "w6_fail": "w6_fail", END: END},
    )
    graph.add_edge("w6_fail", END)
    # 注意:不给 dispatcher 加静态边 —— 实测(1.2.10)节点返回 Command(goto=[Send...])
    # 时静态边会同时生效,导致主管在分支执行中被再次调度;分支各自经 worker → supervisor 回环
    for name in workers:
        graph.add_edge(name, "supervisor")
    graph.add_edge("w1", "supervisor")

    return graph.compile(checkpointer=checkpointer or MemorySaver())
