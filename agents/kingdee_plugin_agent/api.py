"""Web 入口:FastAPI + SSE 实时进度;鉴权复用 sentiment-query-agent auth.py 模式(X-API-Key 头)。

设计见 docs/superpowers/specs/2026-08-08-kingdee-plugin-agent-design.md §6/§7。

接口:
  POST /tasks                    建任务(apikey 鉴权 + KD_* 环境硬门槛)→ 后台线程跑图
  GET  /tasks/{id}/events        SSE 状态流(todo/interrupt/acceptance/done/error),重连自动重放
  GET  /tasks/{id}/state         全量状态快照(断线重连兜底)
  POST /tasks/{id}/answers       澄清回答/确认 → interrupt resume(Command(resume=...))
  POST /tasks/{id}/acceptance    artifact 验收 accept/reject + 原因 → 拒绝原因喂 w7 经验库
  POST /tasks/{id}/feedback      部署后行为错误手动上报 → 原因喂经验库(DEPLOY 通道,设计 §12)

v1 约定:
  - 任务存储 = 内存 dict(app.state.tasks,进程内有效;重启丢失,后续换持久化存储)
  - 每任务独立 build_graph + MemorySaver checkpointer(thread_id 唯一)
  - 图在后台线程执行(与 C11 CLI 同一交互模型):interrupt 挂起 → SSE 推送 → answers 恢复
  - 环境硬门槛:KD_BASE_URL + KD_USERNAME + KD_PASSWORD + KD_DATA_CENTER 4 项全校验,
    任一缺失 → 503 并点明缺项(C11 复审 carry-over,不只查 KD_BASE_URL)

apikey 来源(优先级):create_app(api_key=...) 显式参数 > 环境 KINGDEE_API_KEY >
API_KEYS_JSON 首个 key(与 sentiment auth.py 同源);未配置有效 key 时默认拒绝(401)。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import datetime

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langgraph.types import Command
from sse_starlette.sse import EventSourceResponse

from agents.kingdee_plugin_agent.agent import build_graph, default_recursion_limit
from common import config
from common.otel import init_otel

logger = logging.getLogger(__name__)

#: 环境硬门槛:金蝶环境 4 项全齐才放行建任务(缺任一项 → 503)
_KD_ENV_VARS = ("KD_BASE_URL", "KD_USERNAME", "KD_PASSWORD", "KD_DATA_CENTER")
#: answers 端点等待图进入 interrupt 挂起态的上限(秒);超时说明图仍在执行,409 让客户端重试
_ANSWER_WAIT_S = 30
#: 任务 id 长度(12 hex 可读,够 v1 单进程规模)
_TASK_ID_LEN = 12


def _apikey_from_json() -> str | None:
    """apikey 兜底:API_KEYS_JSON(apikey→用户 映射,复用 sentiment auth.py 数据源)。"""
    raw = config.get_env("API_KEYS_JSON")
    if not raw:
        return None
    try:
        keys = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(keys, dict) and keys:
        return next(iter(keys))
    if isinstance(keys, list) and keys:
        return str(keys[0])
    return None


def _subtask_dict(item) -> dict:
    """todo 条目 → dict:兼容 Subtask 实例 / dict(LangGraph 反序列化)。"""
    if isinstance(item, dict):
        return item
    return {k: v for k, v in vars(item).items() if not k.startswith("_")}


def _todo_list(state) -> list[dict]:
    """从图状态取 todo 列表(初始 dict / 结果 dict;Command 恢复期间无 todo,给空)。"""
    if not isinstance(state, dict):
        return []
    return [_subtask_dict(t) for t in state.get("todo", [])]


class TaskHandle:
    """单任务运行时:图 + checkpointer 会话 + 后台执行线程 + SSE 事件流。

    线程模型:v1 每任务一个 daemon 后台线程跑图(interrupt 时在 Condition 上等待
    恢复);HTTP 线程读快照/投递恢复。所有共享字段改动都持 self._cond 锁;
    事件经「已发列表(重连重放)+ 订阅队列(实时推送,seq 去重)」双通道输出。
    """

    def __init__(self, task_id: str, graph, cfg: dict, initial_state: dict,
                 experience=None):
        self.task_id = task_id
        self.graph = graph
        self.cfg = cfg
        self.state = initial_state        # 最新图结果(dict,始终是 dict;恢复命令不进 state)
        self.experience = experience      # w7 经验库(验收拒绝原因喂入;None = 跳过)
        self.done = False
        self.error: str | None = None
        self.interrupt = None             # 当前挂起 interrupt payload(waiting 时非 None)
        self.waiting = False              # 图正等在 interrupt 上(answers 只在此态投递)
        self.acceptance = None            # 验收结论 {accepted, reason, at}(覆盖语义)
        self.cancelled = False
        self._resume: str | None = None
        self._cond = threading.Condition()
        self._seq = 0
        self._events: list[dict] = []     # {"seq", "event", "data"} 已发事件(重连重放)
        self._subscribers: list[callable] = []

    # ── 后台线程回调(持锁写)───────────────────────────────────────────

    def _emit(self, event: str, data) -> None:
        """记录事件 + 推送给当前订阅者(SSE 连接)。"""
        with self._cond:
            self._seq += 1
            evt = {"seq": self._seq, "event": event, "data": data}
            self._events.append(evt)
            subs = list(self._subscribers)
        for push in subs:
            push(evt)

    def _set_interrupt(self, payload) -> None:
        """图挂起:记录 interrupt + 置 waiting(answers 端点据此投递恢复)。"""
        with self._cond:
            self.interrupt = payload
            self.waiting = True
        self._emit("interrupt", payload)

    def _set_done(self) -> None:
        """图正常结束:发 done 事件 + 关闭订阅流。"""
        with self._cond:
            self.done = True
            self.interrupt = None
            self.waiting = False
            subs = list(self._subscribers)
        self._emit("done", self.snapshot())
        for push in subs:
            push(None)  # sentinel:结束 SSE

    def _set_error(self, message: str) -> None:
        """图执行异常:发 error 事件 + 关闭订阅流(不静默)。"""
        with self._cond:
            self.error = message
            self.interrupt = None
            self.waiting = False
            subs = list(self._subscribers)
        self._emit("error", self.snapshot())
        for push in subs:
            push(None)

    # ── HTTP 侧读取/投递(持锁)────────────────────────────────────────

    def snapshot(self) -> dict:
        """全量状态快照(/state 与 done/error 事件共用,断线重连兜底)。"""
        with self._cond:
            if self.error:
                status = "error"
            elif self.done:
                status = "done"
            elif self.waiting:
                status = "waiting"
            else:
                status = "running"
            return {
                "task_id": self.task_id,
                "status": status,
                "done": self.done,
                "error": self.error,
                "interrupt": self.interrupt,
                "todo": _todo_list(self.state),
                "final_deliverables": (list(self.state.get("final_deliverables") or [])
                                       if isinstance(self.state, dict) else []),
                "acceptance": self.acceptance,
            }

    def deliver_answer(self, answer: str) -> None:
        """投递澄清答复:仅在图挂起时生效;图仍在执行则等待,超时/已结束抛 409。

        需求版本冻结(设计 §8):spec 确认后 requirement_spec 不可变,answers 只接受
        执行中 ask_user 问题的恢复(图对 ask_user 的 resume 只记 user_feedback,
        绝不写 requirement_spec);question/confirm 类型 interrupt 只出现在确认前
        (未确认可继续改 spec)。确认后若收到非 ask_user 类型的恢复输入,说明
        客户端试图把输入解释成 spec 修改路径 → 409 拒绝(防未来回归松动冻结)。
        """
        with self._cond:
            if self.done or self.error:
                raise HTTPException(409, "任务已结束,无需回答")
            if not self._cond.wait_for(lambda: self.waiting, timeout=_ANSWER_WAIT_S):
                raise HTTPException(409, "任务当前未等待输入(可能仍在执行中),稍后重试")
            confirmed = bool((self.state or {}).get("spec_confirmed")) \
                if isinstance(self.state, dict) else False
            itype = (self.interrupt or {}).get("type", "") \
                if isinstance(self.interrupt, dict) else ""
            if confirmed and itype != "ask_user":
                raise HTTPException(409, "需求已确认并冻结,不能修改需求;如要修改请开新任务")
            self._resume = str(answer)
            self._cond.notify_all()

    def record_feedback(self, reason: str) -> dict:
        """部署后行为错误手动上报(设计 §12 反馈通道):原因喂经验库 DEPLOY 通道。

        与验收拒绝同沉淀模式(proposed 态 + sha256 摘要入签名去重):
        签名 `DEPLOY|sha256(reason)[:12]`,不同原因各自累计、相同原因去重;
        沉淀失败不阻塞反馈(never blocks,失败只记日志)。
        """
        verdict = {"reason": str(reason or ""), "at": datetime.now().isoformat(timespec="seconds")}
        if reason and self.experience is not None:
            try:
                sig_part = hashlib.sha256(str(reason).encode("utf-8")).hexdigest()[:12]
                self.experience.propose("DEPLOY", sig_part, str(reason),
                                        "部署后行为错误手动上报,待人工核验(反馈通道)")
            except Exception as exc:  # 沉淀失败不阻塞反馈(与验收同语义)
                logger.warning("service=kingdee-plugin-agent event=feedback_distill_failed "
                               "task_id=%s error=%s", self.task_id, exc)
        self._emit("feedback", verdict)
        return verdict

    def record_acceptance(self, accepted: bool, reason: str) -> dict:
        """记录验收结论;拒绝 + 原因 → 喂 w7 经验库(proposed 态,失败不阻塞验收)。"""
        verdict = {"accepted": bool(accepted), "reason": str(reason or ""),
                   "at": datetime.now().isoformat(timespec="seconds")}
        with self._cond:
            self.acceptance = verdict
        if not accepted and reason and self.experience is not None:
            try:
                # 拒绝原因 = 需求符合性闸门失败样本,w7 同一沉淀通道。签名必须
                # reason 感知(sha256 摘要入 file_pattern):ExperienceStore 按
                # "code|file_pattern" 去重,恒空 file_pattern 会让所有拒绝共享
                # 同一签名 "ARTIFACT|",不同拒绝原因被去重吞掉(复审 Important);
                # 同原因重复拒绝仍去重,不同原因各自累计。
                sig_part = hashlib.sha256(reason.encode("utf-8")).hexdigest()[:12]
                self.experience.propose("ARTIFACT", sig_part, reason,
                                        "artifact 验收拒绝原因,待人工核验(w7)")
            except Exception as exc:  # 沉淀失败不阻塞验收(与 w7 不阻塞交付同语义)
                logger.warning("service=kingdee-plugin-agent event=acceptance_distill_failed "
                               "task_id=%s error=%s", self.task_id, exc)
        self._emit("acceptance", verdict)
        return verdict


def _run_loop(handle: TaskHandle) -> None:
    """后台线程主循环:invoke → 事件推送 → interrupt 挂起等恢复 → resume。

    与 C11 CLI 同一交互模型(interrupt 挂起 → 答复 → Command(resume=...)),
    只是输入从 stdin 换成 answers 端点投递的 _resume。

    注意:恢复命令只作为下一次 invoke 的局部输入,绝不写进 handle.state ——
    state 始终是 dict(全量快照 /state 与 SSE 随时可读,Command 瞬时态不可见)。
    """
    invoke_input = handle.state           # 初始为需求 dict;挂起恢复后为 Command(resume=...)
    while not handle.cancelled:
        try:
            result = handle.graph.invoke(invoke_input, handle.cfg)
        except Exception as exc:
            logger.exception("service=kingdee-plugin-agent event=graph_failed task_id=%s",
                             handle.task_id)
            handle._set_error(f"图执行失败: {exc}")
            return
        with handle._cond:
            handle.state = result
        handle._emit("todo", _todo_list(result))
        interrupts = result.get("__interrupt__") or []
        if not interrupts:
            handle._set_done()
            return
        handle._set_interrupt(interrupts[0].value)
        with handle._cond:
            handle._cond.wait_for(lambda: handle._resume is not None or handle.cancelled)
            if handle.cancelled:
                return
            resume = handle._resume
            handle._resume = None
            handle.waiting = False
        invoke_input = Command(resume=resume)


def create_app(api_key: str | None = None, *, graph_factory=None,
               experience=None) -> FastAPI:
    """构建 Web API。

    Args:
        api_key: 期望 apikey;None = 从环境兜底(KINGDEE_API_KEY / API_KEYS_JSON 首个),
            仍无 → 默认拒绝全部请求(401)。
        graph_factory: 每任务调用一次返回编译好的图(缺省 build_graph() 生产接线;
            测试注入 llm=None + fake 编译/冒烟的确定性图,与 C11 CLI 同思路)。
        experience: w7 经验库(验收拒绝原因喂入;None = 跳过沉淀,验收仍记录)。
    """
    app = FastAPI(title="kingdee-plugin-agent")
    # OTel 初始化(与 sentiment api.py 同款):OTEL_ENDPOINT 配置了才上报;
    # 未配置返回空 provider(span 丢弃),本地无 collector 不阻塞
    init_otel()
    # CORS:允许前端演示页(web/kingdee-demo.html)跨域访问(与 sentiment api.py 同款)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 演示环境放开;生产按需收紧
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.tasks = {}  # task_id → TaskHandle(内存任务存储,v1)

    effective_key = api_key if api_key is not None else (
        config.get_env("KINGDEE_API_KEY") or _apikey_from_json())
    graph_factory = graph_factory or (lambda: build_graph())

    def _check(x_api_key: str) -> None:
        """apikey 校验:X-API-Key 头;未配置有效 key 一律 401(默认拒绝)。"""
        if not effective_key or x_api_key != effective_key:
            raise HTTPException(401, "apikey 无效")

    def _get_task_or_404(task_id: str) -> TaskHandle:
        handle = app.state.tasks.get(task_id)
        if handle is None:
            raise HTTPException(404, f"任务不存在: {task_id}")
        return handle

    @app.post("/tasks")
    def create_task(payload: dict, x_api_key: str = Header(default="")):
        """建任务:apikey 鉴权 → 环境硬门槛(KD_* 4 项)→ 后台线程启动图。"""
        _check(x_api_key)
        missing = [name for name in _KD_ENV_VARS if not config.get_env(name)]
        if missing:
            raise HTTPException(
                503,
                f"金蝶环境未配置完整,缺少: {', '.join(missing)};"
                f"请配置全部 4 项({', '.join(_KD_ENV_VARS)})后再创建任务",
            )
        requirement = str(payload.get("requirement") or "").strip()
        if not requirement:
            raise HTTPException(400, "requirement 必填")
        env_name = str(payload.get("env") or "test")
        task_id = uuid.uuid4().hex[:_TASK_ID_LEN]
        graph = graph_factory()
        # thread_id 每任务唯一(隔离 checkpointer 会话);recursion_limit 按子任务
        # 数预算(与 CLI 同:澄清期未知子任务数,按上限 10 给足)
        cfg = {"configurable": {"thread_id": f"kingdee-api-{task_id}"},
               "recursion_limit": default_recursion_limit(10)}
        # started_at: 任务创建时间戳,驱动全流程时间预算总闸(设计 §8)。
        # 存于 state 而非 thread_id:挂起 resume 后 checkpointer 恢复同一份值,不重置。
        state = {"requirement_spec": {"requirement": requirement,
                                      "environment": env_name},
                 "todo": [],
                 # 目标环境名记录进 state.environment(冒烟/打包等节点可感知;
                 # v1 单环境只记录,不做环境级差异化,见 agent CLAUDE.md 债务)
                 "environment": {"env_name": env_name},
                 "started_at": time.time()}
        handle = TaskHandle(task_id, graph, cfg, state, experience=experience)
        app.state.tasks[task_id] = handle
        threading.Thread(target=_run_loop, args=(handle,), daemon=True).start()
        return {"task_id": task_id, "status": "created"}

    @app.get("/tasks/{task_id}/events")
    async def events(task_id: str, x_api_key: str = Header(default="")):
        """SSE 状态流:todo/interrupt/acceptance/done/error。

        断线重连兜底:连接时先重放已发事件(按 seq 与实时推送去重),再实时推送;
        任务结束后流自动关闭(客户端可转 /state 取全量)。
        """
        _check(x_api_key)
        handle = _get_task_or_404(task_id)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _push(evt):
            loop.call_soon_threadsafe(queue.put_nowait, evt)

        with handle._cond:
            if not (handle.done or handle.error):
                handle._subscribers.append(_push)
            replay = list(handle._events)
            ended = handle.done or handle.error

        async def gen():
            try:
                seen = 0
                for evt in replay:          # 先重放(订阅在前,重叠窗口按 seq 去重)
                    yield {"event": evt["event"],
                           "data": json.dumps(evt["data"], ensure_ascii=False)}
                    seen = evt["seq"]
                if ended:
                    return
                while True:
                    evt = await queue.get()
                    if evt is None:         # sentinel:任务结束,关闭流
                        return
                    if evt["seq"] <= seen:
                        continue            # 重放与实时窗口重叠,去重
                    seen = evt["seq"]
                    yield {"event": evt["event"],
                           "data": json.dumps(evt["data"], ensure_ascii=False)}
            finally:
                with handle._cond:
                    if _push in handle._subscribers:
                        handle._subscribers.remove(_push)

        return EventSourceResponse(gen())

    @app.get("/tasks/{task_id}/state")
    def state(task_id: str, x_api_key: str = Header(default="")):
        """全量状态快照:todo/status/interrupt/acceptance/error(断线重连兜底)。"""
        _check(x_api_key)
        return _get_task_or_404(task_id).snapshot()

    @app.post("/tasks/{task_id}/answers")
    def answer(task_id: str, payload: dict, x_api_key: str = Header(default="")):
        """澄清回答/确认 → Command(resume=answer) 恢复图(与 CLI stdin 同语义)。"""
        _check(x_api_key)
        handle = _get_task_or_404(task_id)
        answer_text = str(payload.get("answer") or payload.get("text") or "").strip()
        if not answer_text:
            raise HTTPException(400, "answer 必填(澄清回答/确认文本)")
        handle.deliver_answer(answer_text)
        return {"ok": True, "task_id": task_id}

    @app.post("/tasks/{task_id}/acceptance")
    def acceptance(task_id: str, payload: dict, x_api_key: str = Header(default="")):
        """artifact 验收:accept/reject + 原因;拒绝原因喂 w7 经验库(proposed 态)。"""
        _check(x_api_key)
        handle = _get_task_or_404(task_id)
        verdict = handle.record_acceptance(bool(payload.get("accepted")),
                                           str(payload.get("reason") or ""))
        return {"ok": True, "task_id": task_id, "acceptance": verdict}

    @app.post("/tasks/{task_id}/feedback")
    def feedback(task_id: str, payload: dict, x_api_key: str = Header(default="")):
        """部署后行为错误手动上报(设计 §12):原因喂经验库 DEPLOY 通道,从不阻塞。"""
        _check(x_api_key)
        handle = _get_task_or_404(task_id)
        verdict = handle.record_feedback(str(payload.get("reason") or ""))
        return {"ok": True, "task_id": task_id, "feedback": verdict}

    return app
