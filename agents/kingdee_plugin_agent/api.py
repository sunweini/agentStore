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
  - 任务存储 = SQLite 持久化(重启恢复):checkpointer 用同步 SqliteSaver(共享连接,
    check_same_thread=False + 内部锁,官方 docstring 确认线程安全 —— 同步版贴合
    后台线程 graph.invoke 架构,无需 ainvoke/asyncio.run 包装,选同步版);
    tasks 元数据表(任务 id/env/status/created_at/requirement)驱动启动恢复
  - 每任务独立 build_graph + 共享 SqliteSaver checkpointer(thread_id 每任务唯一)
  - 图在后台线程执行(与 C11 CLI 同一交互模型):interrupt 挂起 → SSE 推送 → answers 恢复
  - 并发闸门:同时运行任务 ≤ KINGDEE_MAX_CONCURRENT(默认 4),占满 → 429「并发任务数已达上限」;
    恢复的任务非阻塞 acquire(配额不足跳过留待下次重启),_run_loop finally 统一 release 配对
  - 环境硬门槛:按 payload["env"] 分套取凭证(KD_*_<ENV>,空 = 默认 KD_*),
    KD_BASE_URL + KD_USERNAME + KD_PASSWORD + KD_DATA_CENTER 4 项全校验,
    任一缺失 → 503 并点明带后缀缺项(C11 复审 carry-over,不只查 KD_BASE_URL)

apikey 来源(优先级):create_app(api_key=...) 显式参数 > 环境 KINGDEE_API_KEY >
API_KEYS_JSON 首个 key(与 sentiment auth.py 同源);未配置有效 key 时默认拒绝(401)。

持久化(本文件):TaskState/Subtask dataclass 经 JsonPlusSerializer(msgpack)序列化,
Task 5 实测兼容;显式 allowlist(msgpack 白名单)消除「unregistered type」反序列化警告
(默认宽松模式未来版本会收紧,提前显式登记)。恢复语义:启动扫描 tasks 表 status='created'
的任务,按 env 重建图 + checkpoint 读回原 state,后台线程续跑 —— checkpoint 已落盘
的任务 fresh-run 重放挂点(get_state 原 state 作输入,started_at 保留原值不重置,
时间预算不重新计时),checkpoint 缺失(建任务后线程未跑即崩溃)的任务用元数据表
重建初始 state 从头跑。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from sse_starlette.sse import EventSourceResponse

from agents.kingdee_plugin_agent.agent import build_graph, default_recursion_limit
from agents.kingdee_plugin_agent.graph.state import Subtask, TaskState
from common import config
from common.config import kingdee_env_vars
from common.otel import init_otel

logger = logging.getLogger(__name__)

#: 环境硬门槛:金蝶环境 4 项全齐才放行建任务(缺任一项 → 503);
#: 仅查 4 项(KD_LCID 可选,不设门槛)。按 env 分套取(KD_*_<ENV>,空回落 KD_*)。
_KD_ENV_VARS = ("KD_BASE_URL", "KD_USERNAME", "KD_PASSWORD", "KD_DATA_CENTER")
#: 并发闸门:同时运行的任务数上限(默认 4,KINGDEE_MAX_CONCURRENT 可配)。
#: acquire 发生在 create_task 请求处理线程(429 即时响应,不进后台线程)。
def _max_concurrent_tasks() -> int:
    """并发容量:KINGDEE_MAX_CONCURRENT 环境配置;非数字回落默认 4。

    (模块加载期解析,配置写错即抛 ValueError 会 500 全 API —— 回落保证
    服务可起,运维配错只影响并发上限而非可用性。)
    """
    raw = config.get_env("KINGDEE_MAX_CONCURRENT", "4")
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("service=kingdee-plugin-agent event=max_concurrent_invalid "
                       "value=%s fallback=4", raw)
        return 4


MAX_CONCURRENT_TASKS = _max_concurrent_tasks()
_sem = threading.Semaphore(MAX_CONCURRENT_TASKS)
#: answers 端点等待图进入 interrupt 挂起态的上限(秒);超时说明图仍在执行,409 让客户端重试
_ANSWER_WAIT_S = 30
#: 任务 id 长度(12 hex 可读,够 v1 单进程规模)
_TASK_ID_LEN = 12
#: 任务元数据库默认路径(相对 CWD,与 data/kingdee-deliverables 同惯例;KINGDEE_TASKS_DB 可覆盖)
_TASKS_DB_DEFAULT = "data/kingdee-tasks.db"
#: 任务元数据表:驱动启动恢复(status='created' 未完成任务)。created_at 为 ISO 时间串。
_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    env TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'created',
    created_at TEXT NOT NULL,
    requirement TEXT NOT NULL,
    claimed_at REAL
)
"""
#: 共享 msgpack 白名单 serde:TaskState/Subtask dataclass 显式登记,
#: 消除反序列化 unregistered 警告(LANGGRAPH_STRICT_MSGPACK 未来默认开启的兼容准备)。
_TASK_SERDE = JsonPlusSerializer(allowed_msgpack_modules=[Subtask, TaskState])


def _db_path(db_path: str | None) -> str:
    """任务库路径:显式参数 > KINGDEE_TASKS_DB 环境 > 默认 data/kingdee-tasks.db。"""
    return db_path or config.get_env("KINGDEE_TASKS_DB", _TASKS_DB_DEFAULT)


def _make_saver(db_path: str) -> SqliteSaver:
    """共享 SQLite checkpointer:多线程(graph 后台线程 + 恢复扫描)同一连接写入。

    SqliteSaver 内部持 threading.Lock 保证线程安全,连接必须 check_same_thread=False
    (官方 docstring 明确该组合安全);setup() 幂等建表。本文件用同步 graph.invoke
    (后台线程架构),同步版无需 ainvoke/asyncio.run 包装。
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    saver = SqliteSaver(sqlite3.connect(db_path, check_same_thread=False),
                        serde=_TASK_SERDE)
    saver.setup()
    return saver


def _task_conn(db_path: str) -> sqlite3.Connection:
    """元数据表连接(短生命周期,每次操作独立 open/close)。

    低频操作(建任务/任务结束置位/启动扫描),独立连接避免与 checkpointer 共享
    连接时的锁竞争;DDL 幂等随连接执行(表已存在不重建)。
    """
    conn = sqlite3.connect(db_path)
    conn.execute(_TASKS_DDL)
    # 轻量迁移:v1.21.x 早期表无 claimed_at 列(B-1 时间戳回收新增)——
    # 老表补列,不迁移不丢列即认领 SQL 崩(ALTER 幂等仅一次,加列后不再执行)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if cols and "claimed_at" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN claimed_at REAL")
    return conn


def _insert_task(db_path: str, task_id: str, env: str, requirement: str) -> None:
    """建任务记录(默认 status='created'):启动恢复据此扫描未完成任务。

    用 INSERT OR IGNORE:恢复路径重建 handle 时行已存在(读取自同一表),
    幂等保留原记录(created_at/env/requirement 以首次创建为准)。
    """
    with closing(_task_conn(db_path)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tasks (id, env, status, created_at, requirement) "
            "VALUES (?, ?, 'created', ?, ?)",
            (task_id, env, datetime.now().isoformat(timespec="seconds"), requirement))
        conn.commit()


def _update_task_status(db_path: str, task_id: str, status: str) -> None:
    """任务终态置位(done/error):终止重启恢复误扫已结束任务。"""
    with closing(_task_conn(db_path)) as conn:
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
        conn.commit()


def _clear_claimed_at(db_path: str, task_id: str) -> None:
    """恢复配额不足/失败回退时清占位时间戳(任务回 created 保持可恢复)。

    恢复认领的占位(running + claimed_at=本次启动)在任务回到 created 时一并
    清掉 —— 否则下次重启陈旧回收仍能认领(claimed_at 早于新 boot_ts,语义
    等价),但保持表状态干净:created 任务无 claimed_at 归属,不误导排查。
    """
    with closing(_task_conn(db_path)) as conn:
        conn.execute("UPDATE tasks SET claimed_at=NULL WHERE id=?", (task_id,))
        conn.commit()


def _pending_task_rows(db_path: str) -> list[tuple[str, str, str]]:
    """启动恢复扫描:未完成任务 (id, env, requirement)。

    扫 status IN ('created','running'):created = 建任务后线程未跑即崩溃,
    running = 上个进程认领后崩溃/挂起未落终态(恢复最常见结果:任务恢复后
    再次 interrupt 挂起等用户回答,DB 停留在 running)。只扫 created 会丢
    running 任务(恢复成功的反而丢失,被跳过的反而保留)。
    """
    with closing(_task_conn(db_path)) as conn:
        return conn.execute(
            "SELECT id, env, requirement FROM tasks "
            "WHERE status IN ('created','running')"
        ).fetchall()


def _claim_pending_task(db_path: str, task_id: str, boot_ts: float) -> bool:
    """恢复前占位:created/running → running + claimed_at,任务级幂等 + 陈旧回收。

    返回 True = 本实例成功认领(继续恢复);False = 行不存在 / 并发实例刚认领
    (claimed_at 新鲜),跳过该任务 —— 防同一 DB 双实例并发扫描读到同一行,
    对同一 thread_id 双线程 invoke(checkpoint 竞态损坏 / 双倍计费)。

    陈旧判定:claimed_at 早于本次启动 boot_ts = 上个进程遗留(崩溃/挂起),
    可回收重认领;晚于本次启动 = 并发实例刚认领,不回收(防双跑)。快速重启
    (秒级)也正确 —— 上个进程的 claimed_at 必然早于新 boot_ts。双实例并发
    启动窗口内的竞态 v1 接受(单实例部署设计约束)。
    """
    with closing(_task_conn(db_path)) as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='running', claimed_at=? "
            "WHERE id=? AND status IN ('created','running') "
            "AND (claimed_at IS NULL OR claimed_at < ?)",
            (boot_ts, task_id, boot_ts))
        conn.commit()
        return cur.rowcount > 0


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
                 experience=None, db_path: str | None = None):
        self.task_id = task_id
        self.graph = graph
        self.cfg = cfg
        self.state = initial_state        # 最新图结果(dict,始终是 dict;恢复命令不进 state)
        self.experience = experience      # w7 经验库(验收拒绝原因喂入;None = 跳过)
        self.db_path = db_path            # 任务元数据库路径(终态置位用;None = 不落盘)
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

    配额模型(Task 5 持久化后):create_task 在请求线程 acquire(占满 429);
    恢复的任务在恢复线程非阻塞 acquire(配额不足跳过,回写 created 留待
    下次重启 —— 恢复语义是「不漏任务」而非「限制恢复」)。统一由本函数
    finally release 配对,不泄漏;结束/失败/取消写元数据表终态,防止重启
    误恢复已结束任务。
    """
    try:
        invoke_input = handle.state   # 初始为需求 dict;挂起恢复后为 Command(resume=...)
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
    finally:
        # 归还并发闸门配额(任务结束/失败/取消统一走这里,不泄漏信号量)
        _sem.release()
        # 终态落盘:重启恢复只扫 status='created'(建任务记录)。cancel 路径
        # (handle.cancelled 直接 return 不进 _set_done)同样落终态 —— 不落的话
        # 任务在 DB 里永远 created,每次重启都重建 handle 重放(checkpoint 会话
        # 虽一致,但重复执行有双倍计费/重复冒烟副作用)。写入失败不阻塞线程
        # 退出(任务本体已完成,元数据缺失仅影响恢复语义,记日志待排查)。
        final_status = ("cancelled" if handle.cancelled
                        else "done" if handle.done else "error")
        try:
            _update_task_status(handle.db_path, handle.task_id, final_status)
        except Exception as exc:
            logger.warning("service=kingdee-plugin-agent event=task_status_persist_failed "
                           "task_id=%s status=%s error=%s", handle.task_id,
                           final_status, exc)


def create_app(api_key: str | None = None, *, graph_factory=None,
               experience=None, db_path: str | None = None) -> FastAPI:
    """构建 Web API。

    Args:
        api_key: 期望 apikey;None = 从环境兜底(KINGDEE_API_KEY / API_KEYS_JSON 首个),
            仍无 → 默认拒绝全部请求(401)。
        graph_factory: 每任务调用一次返回编译好的图(注入 = 测试确定性图 llm=None +
            fake 编译/冒烟,与 C11 CLI 同思路);None = 生产缺省 build_graph(env=env_name),
            env 由 create_task 按 payload["env"] 透传(空 = 默认凭证套)。
        experience: w7 经验库(验收拒绝原因喂入;None = 跳过沉淀,验收仍记录)。
        db_path: 任务元数据库路径;None = KINGDEE_TASKS_DB 环境 > 默认 data/kingdee-tasks.db。
            启动时扫描未完成任务重建 handle 续跑(持久化恢复)。
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
    app.state.tasks = {}  # task_id → TaskHandle(进程内运行期任务表;重启由 DB 恢复重建)

    path = _db_path(db_path)
    # 共享 checkpointer:连接在 create_app 生命周期常驻(setup 建表幂等)。
    # 单例语义 = 所有任务同一 thread_id 隔离会话(checkpointer 表按 thread_id 分);
    # graph_factory 注入的图若自带 checkpointer(测试图),覆盖共享实例。
    app.state.saver = _make_saver(path)

    effective_key = api_key if api_key is not None else (
        config.get_env("KINGDEE_API_KEY") or _apikey_from_json())
    # 注意:graph_factory 不做预置(不 `or (lambda: build_graph())`)—— 预置会让
    # create_task 的 `graph_factory() if graph_factory else build_graph(env=...)`
    # else 分支恒不可达,env 永不透传;生产路径必须走 build_graph(env=env_name)。

    def _check(x_api_key: str) -> None:
        """apikey 校验:compare_digest 恒定时间比较;未配置有效 key 一律 401。"""
        if not effective_key or not secrets.compare_digest(
                x_api_key.encode(), effective_key.encode()):
            raise HTTPException(401, "apikey 无效")

    def _get_task_or_404(task_id: str) -> TaskHandle:
        handle = app.state.tasks.get(task_id)
        if handle is None:
            raise HTTPException(404, f"任务不存在: {task_id}")
        return handle

    def _make_handle(task_id: str, graph, env_name: str,
                     requirement: str, initial_state: dict) -> TaskHandle:
        """构造 TaskHandle + 落盘元数据(建任务与恢复共用同一入口)。

        生产图调用方已用共享 checkpointer 编译(build_graph(checkpointer=
        app.state.saver),MemorySaver 默认换掉,重启可见同一 checkpoint 会话);
        注入图(graph_factory,测试)自带 checkpointer 原样使用 —— 已编译图
        (CompiledStateGraph)没有 .compile,任何二次 compile 都会挂。
        """
        cfg = {"configurable": {"thread_id": f"kingdee-api-{task_id}"},
               "recursion_limit": default_recursion_limit(10)}
        handle = TaskHandle(task_id, graph, cfg, initial_state,
                            experience=experience, db_path=path)
        app.state.tasks[task_id] = handle
        _insert_task(path, task_id, env_name, requirement)
        return handle

    def _run_thread(handle: TaskHandle) -> None:
        """启动后台线程跑图(建任务与恢复共用)。"""
        threading.Thread(target=_run_loop, args=(handle,), daemon=True).start()

    def _restore_pending() -> None:
        """启动恢复:扫元数据表未完成任务,按 env 重建图 + handle 续跑。

        时序:create_app 启动(uvicorn 加载)即恢复,线程失败不影响 app 启动。
        恢复输入语义(与 _run_loop 第一轮一致:plain dict 重放挂点):
        checkpoint 已落盘(interrupt 挂起/中途)的任务,用 get_state 读回完整
        checkpoint state 作为输入 —— fresh-run 重放,挂点正确(started_at 保留
        原值,时间预算不重置,设计 §8「挂起 resume 不重置」;用新 time.time()
        会覆盖 checkpoint 值,恢复任务从重启时刻重新计时,违反冻结语义;
        todo 经 reducer 合并、spec 完整保留,不用元数据表简化 dict 覆盖);
        metrics 键排除(求和 reducer,输入同值会双计翻倍,见恢复输入处注释);
        checkpoint 缺失(建任务后线程未跑即崩溃)的任务用元数据表构造初始
        state 从头跑。
        并发闸门:恢复路径用非阻塞 acquire(blocking=False),失败跳过该任务
        (元数据回写 created,留待下次重启/手动恢复)—— 不能阻塞 acquire:
        恢复线程挂在 interrupt 等用户回答期间不释放配额,挂起任务数 >
        KINGDEE_MAX_CONCURRENT 时第 N+1 个任务会永久阻塞在 create_app 内,
        整个 API 起不来(实测复现)。恢复语义是「不漏任务」而非「限制恢复」,
        超限跳过不丢任务(下轮恢复)。
        任务级幂等 + 陈旧回收:恢复前先 UPDATE 占位 created/running →
        running + claimed_at(见 _claim_pending_task),并发实例扫到新鲜
        claimed_at 跳过,防双线程 invoke;claimed_at 早于本次启动(boot_ts)
        = 上个进程遗留,可回收 —— 恢复后的任务再次挂起停在 running 也不丢
        (下次重启仍可回收重认领)。
        """
        boot_ts = time.time()
        try:
            for task_id, env_name, requirement in _pending_task_rows(path):
                try:
                    if not _claim_pending_task(path, task_id, boot_ts):
                        # 已被另一实例认领(占位先行,claimed_at 新鲜,幂等跳过)
                        logger.info("service=kingdee-plugin-agent "
                                    "event=task_restore_skipped_claimed "
                                    "task_id=%s", task_id)
                        continue
                    if not _sem.acquire(blocking=False):
                        # 恢复配额不足:回写 created + 清 claimed_at 保持可恢复,
                        # 跳过等下次(线程未启动,不得 release —— 配额从未被
                        # 本任务持有)
                        _update_task_status(path, task_id, "created")
                        _clear_claimed_at(path, task_id)
                        logger.warning("service=kingdee-plugin-agent "
                                       "event=task_restore_skipped_capacity "
                                       "task_id=%s env=%s", task_id, env_name)
                        continue
                    # 认领成功且配额到手:重建图 + handle + 启动线程。
                    # 认领与启动之间任何失败都归还配额 + 回写 created(保持可恢复,
                    # 下轮重启再试);线程启动成功后由 _run_loop finally release + 落终态。
                    try:
                        graph = graph_factory() if graph_factory \
                            else build_graph(env=env_name,
                                             checkpointer=app.state.saver)
                        # checkpoint 已落盘的任务:读回原 state 作恢复输入(见 docstring);
                        # 缺失则构造新初始 state(从头跑)
                        cfg = {"configurable": {"thread_id": f"kingdee-api-{task_id}"},
                               "recursion_limit": default_recursion_limit(10)}
                        snapshot = graph.get_state(cfg)
                        if snapshot.values:
                            # 恢复输入 = checkpoint 原 state(fresh-run 重放,started_at
                            # 保留不重置);但 metrics 是求和 reducer(_merge_metrics),
                            # 输入带 checkpoint 当前值会被 operator(current, v) 再算
                            # 一次 —— 双计,compile_pass_count 等五计数器恢复后翻倍
                            # (多次重启逐次累计)。去掉 metrics 键 = 该通道不产生输入
                            # 更新,保留 checkpoint 原值(等价 pre-fix 覆盖起算);其余
                            # reducer 通道(todo 按 id 合并 / rework_events 替换 /
                            # final_deliverables 去重追加)对同值输入幂等,无此问题。
                            initial_state = dict(snapshot.values)
                            initial_state.pop("metrics", None)
                        else:
                            initial_state = {"requirement_spec": {"requirement": requirement,
                                                                  "environment": env_name},
                                             "todo": [],
                                             "environment": {"env_name": env_name},
                                             "started_at": time.time()}
                        handle = _make_handle(task_id, graph, env_name,
                                              requirement, initial_state)
                        _run_thread(handle)
                        logger.info("service=kingdee-plugin-agent event=task_restored "
                                    "task_id=%s env=%s", task_id, env_name)
                    except Exception as exc:
                        # 单任务恢复失败不阻断其余任务;线程未启动,归还本任务
                        # 持有的配额(与上方 acquire 配对)+ 回写 created 保持
                        # 可恢复(下轮重启再试)。线程启动成功后配额与终态由
                        # _run_loop finally 统一处理,不会走到这里。
                        _sem.release()
                        try:
                            _update_task_status(path, task_id, "created")
                            _clear_claimed_at(path, task_id)
                        except Exception as persist_exc:  # 回写失败不阻断后续任务
                            logger.warning("service=kingdee-plugin-agent "
                                           "event=task_restore_status_reset_failed "
                                           "task_id=%s error=%s", task_id, persist_exc)
                        logger.error("service=kingdee-plugin-agent event=task_restore_failed "
                                     "task_id=%s env=%s error=%s", task_id, env_name, exc)
                except Exception as exc:
                    # 认领/配额分支异常(DB 读写失败等):此路径未 acquire、未启动
                    # 线程,无需 release;任务保持 created(或回写失败停在 running,
                    # 记日志),下轮重启仍可恢复。单任务失败不阻断其余任务。
                    logger.error("service=kingdee-plugin-agent event=task_restore_failed "
                                 "task_id=%s env=%s error=%s", task_id, env_name, exc)
        except Exception as exc:
            logger.error("service=kingdee-plugin-agent event=task_restore_scan_failed "
                         "error=%s", exc)

    _restore_pending()

    @app.post("/tasks")
    def create_task(payload: dict, x_api_key: str = Header(default="")):
        """建任务:apikey 鉴权 → 并发闸门(429)→ 环境硬门槛(KD_* 4 项按 env 分套,503)→ 后台线程启动图。

        并发闸门:Semaphore 在这里 acquire(请求线程),占满立即 429,
        不进后台线程(线程内 raise 到不了 FastAPI)。
        """
        _check(x_api_key)
        env_name = str(payload.get("env") or "")   # 空 = 默认环境(KD_* 4 项硬门槛)
        if not _sem.acquire(blocking=False):
            raise HTTPException(429, "并发任务数已达上限,稍后重试")
        try:
            vars_ = kingdee_env_vars(env_name)
            missing = [name for name in _KD_ENV_VARS if not vars_.get(name)]
            if missing:
                suffix = f"_{env_name.upper()}" if env_name else ""
                raise HTTPException(
                    503,
                    f"金蝶环境未配置完整,缺少: "
                    f"{', '.join(m + suffix for m in missing)};"
                    f"请配置后再创建任务",
                )
            requirement = str(payload.get("requirement") or "").strip()
            if not requirement:
                raise HTTPException(400, "requirement 必填")
            task_id = uuid.uuid4().hex[:_TASK_ID_LEN]
            graph = graph_factory() if graph_factory \
                else build_graph(env=env_name, checkpointer=app.state.saver)
            # started_at: 任务创建时间戳,驱动全流程时间预算总闸(设计 §8)。
            # 存于 state 而非 thread_id:挂起 resume 后 checkpointer 恢复同一份值,不重置。
            state = {"requirement_spec": {"requirement": requirement,
                                          "environment": env_name},
                     "todo": [],
                     # 目标环境名记录进 state.environment(冒烟/打包等节点可感知;
                     # v1 单环境只记录,不做环境级差异化,见 agent CLAUDE.md 债务)
                     "environment": {"env_name": env_name},
                     "started_at": time.time()}
            handle = _make_handle(task_id, graph, env_name, requirement, state)
            _run_thread(handle)
            return {"task_id": task_id, "status": "created"}
        except BaseException:
            # 线程未启动成功(校验 503/400、build_graph 抛错、Thread.start 抛错等):
            # 归还配额。acquire 在 try 之外,此处不会重复释放;成功路径由
            # _run_loop finally 释放。
            _sem.release()
            raise

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
