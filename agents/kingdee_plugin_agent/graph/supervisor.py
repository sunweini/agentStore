"""主管节点:派发/编排/返工预算/并发上限/终态处理。

决策循环:
  主管 ──► 终态检查(全部交付→finish;失败/预算耗尽→fail)
  ──► 依赖失败传递(依赖 failed → 依赖者标记 failed)
  ──► 可派发 → run:<subtask_id>(确定性派发整个就绪批,≤ MAX_PARALLEL)
  ──► 无可派发 → LLM 结构化决策(ask_user/finish/fail)/ 确定性兜底 ask_user

动作契约(C10):
  run:<subtask_id>   派发一个子任务到其生命周期下一阶段 worker
  ask_user[:<问题>]  挂起问用户(w1 节点 interrupt;问题缺省给通用提示)
  finish             全部子任务 delivered,正常收尾
  fail[:<原因>]      失败收尾(剩余子任务标记 failed)

状态 → worker 映射(子任务生命周期驱动派发,worker 名见 Plan C 接线):
  pending → w2(设计)  design_done → w3(生成)  gen_done → w4(审查)
  needs_rework → w3(重新生成)  review_done → w5(编译)
  compile_done → w5_5(冒烟)  smoke_done → w6(打包)  packaged → w7(沉淀)
  blocked → w1(问用户)  in_progress/delivered/failed → 不再派发

终态处理位置(终审 C4 裁决):放在本 Supervisor.decide(主管扩展),
路由函数只做机械映射;依赖失败传递在派发前做,防止把失败依赖的依赖者派发出去。
"""
import json
from pathlib import Path
from typing import Literal

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from agents.kingdee_plugin_agent.graph.state import TaskState, Subtask, MAX_PARALLEL
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase

#: 子任务状态 → 下一阶段 worker。生命周期见 state.py 模块 docstring。
STATUS_TO_WORKER = {
    "pending": "w2",
    "design_done": "w3",
    "gen_done": "w4",
    "needs_rework": "w3",
    "review_done": "w5",
    "compile_done": "w5_5",
    "smoke_done": "w6",
    "packaged": "w7",
    "blocked": "w1",          # 等用户信息 → 问用户
    "in_progress": None,      # 执行中,不再派发
    "delivered": None,        # 终态
    "failed": None,           # 终态
}


def worker_for_subtask(subtask: Subtask) -> str | None:
    """子任务状态 → 阶段 worker 名(无则 None:终态/执行中,不可派发)。"""
    return STATUS_TO_WORKER.get(subtask.status)


class DecideAction(BaseModel):
    """主管 LLM 结构化决策(经 with_structured_output 绑定)。"""

    action: Literal["run", "ask_user", "finish", "fail"] = "run"
    subtask_id: str = ""      # action == run 时:要派发的子任务 id
    question: str = ""        # action == ask_user 时:问用户的问题


class Supervisor:
    """子任务调度主管。

    - _next_ready:依赖满足 + 并发 < MAX_PARALLEL 时返回下一个可派发的 pending 子任务(单发,C4 契约)
    - _ready_batch:返回可派发批量(≤ 并发上限),prefer 优先(并行派发用)
    - _check_budget:返工预算是否还有余额
    - _cascade_failed:依赖失败的 pending 依赖者标记 failed(传递)
    - _summary_table:把子任务池渲染成 LLM 可读的摘要表
    - decide:决策入口 —— 终态检查(确定性)→ 派发(确定性/LLM 选优)→ 问用户
    """

    def __init__(self, llm, workers: dict[str, WorkerBase]):
        self.llm = llm
        self.workers = workers
        self._prompt_dir = Path(__file__).parent.parent / "prompts"

    def _load_prompt(self, name: str) -> str:
        p = self._prompt_dir / name
        if not p.exists():
            raise FileNotFoundError(f"prompt 缺失: {p}")
        return p.read_text(encoding="utf-8")

    def _summary_table(self, state: TaskState) -> str:
        lines = [f"返工预算剩余: {state.rework_budget_left}"]
        for fb in state.user_feedback[-3:]:
            lines.append(f"  用户反馈: {fb}")
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

    def _ready_batch(self, state: TaskState, prefer: str = "") -> list[Subtask]:
        """返回可派发的子任务批量(生命周期各阶段,并发上限内),prefer 优先排头。

        send() 并行派发用:一轮把**所有可以推进阶段**的子任务全派出去
        (pending → w2 设计、design_done → w3 生成、needs_rework → w3 ……,
        见 STATUS_TO_WORKER)—— 注意不止 pending:worker 完成后子任务进入下一
        阶段状态,主管要能继续派发它;终态(delivered/failed)与执行中
        (in_progress)不派发。pending 依赖者仍需依赖满足才进批。
        """
        running = [s for s in state.todo if s.status == "in_progress"]
        capacity = MAX_PARALLEL - len(running)
        if capacity <= 0:
            return []
        status_by_id = {t.id: t.status for t in state.todo}
        ready: list[Subtask] = []
        for s in state.todo:
            worker = worker_for_subtask(s)
            if worker is None or worker == "w1":
                # 终态不可派发;blocked(worker 映射为 w1)也不进批 —— w1 是交互
                # 节点,不走 Send 分支;blocked 子任务由主管走 ask_user 问用户,
                # 若进批会导致 supervisor↔dispatcher 空派发忙循环(burn 完
                # recursion_limit,终审 C10 review 实测 GraphRecursionError)
                continue
            if s.status == "pending" and not all(
                    status_by_id.get(d) in (None, "packaged", "delivered") for d in s.deps):
                continue  # 依赖未满足的 pending 不派发(非 pending 已通过依赖门)
            ready.append(s)
            if len(ready) >= capacity:
                break
        if prefer:
            preferred = next((s for s in ready if s.id == prefer), None)
            if preferred:
                ready.remove(preferred)
                ready.insert(0, preferred)
        return ready

    def _check_budget(self, state: TaskState) -> bool:
        return state.rework_budget_left > 0

    def _cascade_failed(self, state: TaskState) -> None:
        """依赖失败传递:依赖已 failed 的 pending 子任务标记 failed。

        原地修改 state.todo(节点包装器会把 todo 整体回写)。
        """
        status_by_id = {t.id: t.status for t in state.todo}
        for s in state.todo:
            if s.status == "pending" and any(status_by_id.get(d) == "failed" for d in s.deps):
                s.status = "failed"

    def _all_delivered(self, state: TaskState) -> bool:
        return bool(state.todo) and all(s.status == "delivered" for s in state.todo)

    def decide(self, state: TaskState) -> str:
        """返回动作:run:<subtask_id> | ask_user[:<问题>] | finish | fail[:<原因>]

        顺序(确定性优先,LLM 只做无法机械判定时的选择):
          1. 依赖失败传递(pending 依赖者 → failed)
          2. 全部 delivered → finish
          3. 存在 failed → fail(依赖传递后)
          4. 返工预算耗尽且仍有未交付工作 → fail(剩余标记 failed)
          5. 有可派发 → run:<sid>(LLM 存在时让其选优;确定性兜底派批首)
          6. 无可派发 → LLM 决策(ask_user/finish/fail)或确定性 ask_user
        """
        self._cascade_failed(state)
        if self._all_delivered(state):
            return "finish"
        if any(s.status == "failed" for s in state.todo):
            return "fail:存在失败子任务"
        remaining = [s for s in state.todo if s.status not in ("delivered", "failed")]
        if state.rework_budget_left <= 0 and remaining:
            for s in remaining:
                s.status = "failed"
            return "fail:返工预算耗尽"
        ready = self._ready_batch(state)
        if ready:
            if self.llm is not None:
                return self._llm_choose(state, ready)
            return f"run:{ready[0].id}"
        if self.llm is not None:
            return self._llm_choose(state, [])
        return "ask_user"

    def _llm_choose(self, state: TaskState, ready: list[Subtask]) -> str:
        """LLM 基于摘要表选动作(结构化输出),非法/失败回退确定性派发。

        动作校验(防幻觉):
          run:<sid>  sid 必须是当前可派发集合成员,否则回退派批首
          ask_user   → ask_user[:<问题>]
          finish     → 门控 _all_delivered:澄清期(todo 空)LLM 幻觉 finish 会以
                       零交付结束图(CLI 误报成功),回落确定性兜底而非放行
          fail       → 主管裁量(需求不可实现等),放行
        """
        try:
            prompt = ChatPromptTemplate.from_messages([
                ("system", self._load_prompt("supervisor.md")),
                ("human", "任务上下文:\n{context}\n\n子任务摘要表:\n{table}\n\n"
                          "请输出下一步动作(严格按动作格式)。"),
            ])
            context = json.dumps(state.requirement_spec, ensure_ascii=False)[:2000]
            out = self.llm.with_structured_output(DecideAction).invoke(
                prompt.format_messages(context=context, table=self._summary_table(state)))
        except Exception:
            out = None  # LLM 故障 → 确定性兜底
        if out is None:
            return f"run:{ready[0].id}" if ready else "ask_user"
        a = out.action
        if a == "ask_user":
            return f"ask_user:{out.question}" if out.question else "ask_user"
        if a == "finish" and self._all_delivered(state):
            return "finish"
        if a == "fail":
            return "fail:主管判定需求不可完成"
        if a == "run":
            if out.subtask_id and any(s.id == out.subtask_id for s in ready):
                return f"run:{out.subtask_id}"
            return f"run:{ready[0].id}" if ready else "ask_user"
        return f"run:{ready[0].id}" if ready else "ask_user"
