"""合同审核 agent FastAPI 接口。架构见 design doc §7。

接口清单:
  POST /api/v1/contract/review   上传文件 + contract_type + 审核要求 → task_id(SSE 章节进度)
  GET  /api/v1/contract/status   任务状态(解析中/审核中/完成/失败/取消 + 进度)
  GET  /api/v1/contract/result   最终报告(JSON + markdown)
  POST /api/v1/contract/stop     停止任务(复用 sentiment stop 模式)
  POST /api/v1/contract/prompt   F1:合同类型 + 原始 prompt → 优化后 prompt
  POST /api/v1/laws/upload       用户补充法条库(管理员)
  GET  /api/v1/laws              法条库列表(law_name/条数/版本)
  POST /api/v1/apikeys           独立 apikey 创建(管理员)
  GET  /api/v1/apikeys           独立 apikey 列表(管理员)
  DELETE /api/v1/apikeys/{apikey} 独立 apikey 停用(管理员)
  GET  /health                   健康检查

鉴权/计费走公共组件 common.auth / common.billing(agent='contract',统一表
  agent_api_keys / agent_billing_records):
  所有接口需 Header apikey;apikey 管理接口需管理员。
  审核完成 commit 扣 1 单位;F1 / 法条查询不计费;并发 pending 上限 5。

任务生命周期(进程内存 _tasks + SQLite pending 计费,审查加固后):
  running ──► done / failed / cancelled
  - done:run_review 成功 + billing.commit 扣费成功
  - failed:run_review 返回 error / 抛异常(LLM/检索) / commit 失败 → billing.cancel_pending
  - cancelled:/stop 触发,线程完成前被标记;线程完成时跳过 commit、不覆写状态
  后台线程任何异常都兜底为 failed + cancel_pending + unlink,绝不静默退出
  (否则任务永久 stuck running、pending 槽位泄漏、临时文件残留)。

文件存 tempfile 临时目录,线程结束(完成/失败/取消)一律 unlink;
写盘前先校验大小(≤2MB,防公网 DoS)。
"""
from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, UploadFile, HTTPException
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from common import auth, billing
from agents.contract_review_agent.store.law_store import LawStore

app = FastAPI(title="contract_review_agent")
_law_store = LawStore(
    data_dir=Path("data/contract-rag"),
    laws_dir=Path("agents/contract_review_agent/data/laws"))
_tasks: dict[str, dict] = {}  # task_id -> {status, progress, result, error, apikey, request_id}


@app.get("/contract-demo")
def contract_demo():
    """测试页(web/contract_demo.html),与 API 同源,免 CORS。"""
    return FileResponse(Path("web/contract_demo.html"))
_lock = threading.Lock()

logger = logging.getLogger(__name__)

_SERVICE = "contract_review_agent"
_MAX_BYTES = 2 * 1024 * 1024  # 文件大小上限 2MB(design doc §7)


def _mask(apikey: str) -> str:
    """apikey 脱敏后进日志(凭据不落明文,OBS-CORE-003 精神)。"""
    if not apikey:
        return ""
    return f"{apikey[:6]}***{apikey[-4:]}" if len(apikey) > 12 else "***"


@app.get("/health")
def health():
    return {"status": "ok"}


def _require_key(apikey: str) -> dict:
    """apikey 校验;鉴权失败记结构化日志(OBS-CORE-001/002)。"""
    try:
        return auth.check_apikey(apikey, "contract")
    except HTTPException as exc:
        logger.warning("service=%s event=auth_failed apikey=%s status=%s",
                       _SERVICE, _mask(apikey), exc.status_code)
        raise


def _require_admin(apikey: str) -> None:
    """管理员校验;鉴权失败记结构化日志。"""
    try:
        auth.require_admin(apikey, "contract")
    except HTTPException as exc:
        logger.warning("service=%s event=auth_failed role=admin apikey=%s status=%s",
                       _SERVICE, _mask(apikey), exc.status_code)
        raise


def _task_for(task_id: str, apikey: str) -> dict:
    """按归属取任务:不存在或非本人任务一律 404(不泄露任务存在性)。"""
    with _lock:
        t = _tasks.get(task_id)
    if t is None or t.get("apikey") != apikey:
        raise HTTPException(status_code=404, detail="任务不存在")
    return t


@app.post("/api/v1/contract/prompt")
def contract_prompt(contract_type: str = Form(...), prompt: str = Form(...),
                    apikey: str = Header(...)):
    """F1:合同类型 + 原始审核要求 → 结构化审核 prompt(不计费)。"""
    _require_key(apikey)
    from agents.contract_review_agent.graph.prompt_node import optimize_review_prompt
    return {"prompt": optimize_review_prompt(contract_type, prompt)}


@app.post("/api/v1/laws/upload")
def laws_upload(apikey: str = Header(...), file: UploadFile = File(...)):
    """用户补充法条库:仅管理员。上传文本 seed 入向量库 + 精确索引。"""
    _require_admin(apikey)
    content = file.file.read().decode("utf-8", errors="replace")
    return _law_store.seed(content)


@app.get("/api/v1/laws")
def laws_list(apikey: str = Header(...)):
    """法条库列表(law_name/领域/条数)。"""
    _require_key(apikey)
    return {"laws": _law_store.list_laws()}


def _run_task(task_id: str, file_path: str, contract_type: str,
              prompt: str, apikey: str, request_id: str) -> None:
    """后台线程:跑完整审核流水线 → 更新任务状态 → commit/cancel 计费 → 删临时文件。

    审查加固:
    - run_review 整体 try/except:LLM/检索异常 → failed + cancel_pending + unlink,
      绝不静默退出(Critical)。
    - commit 前检查 status=='cancelled':已取消则跳过 commit、不覆写状态(保留
      cancelled),仍 unlink 临时文件(Important #3)。
    """
    from agents.contract_review_agent.agent import run_review

    def _progress_cb(stage: str, current: int, total: int, title: str = "") -> None:
        """章节级进度回调:更新 _tasks 的 stage/progress,SSE 据此回显。"""
        with _lock:
            t = _tasks.get(task_id)
            if t is None:
                return
            t["progress"] = max(t.get("progress", 0.0),
                                (current / total) if total else 0.0)
            t["stage"] = stage
            t["current"] = current
            t["total"] = total
            if title:
                t["stage_title"] = title

    try:
        result = run_review(file_path, contract_type, prompt,
                            law_store=_law_store, progress_cb=_progress_cb)
    except Exception as exc:
        # 只记异常类型不记 str(exc):异常消息可能携带凭据/文件内容等敏感信息,
        # 直落日志或 t["error"] 会经 result 端点泄露(审查 Important 凭据防护)。
        logger.error("service=%s event=task_failed task_id=%s request_id=%s error_type=%s",
                     _SERVICE, task_id, request_id, type(exc).__name__)
        with _lock:
            t = _tasks.get(task_id)
            cancelled = t is not None and t["status"] == "cancelled"
        if not cancelled:
            with _lock:
                t["status"] = "failed"
                t["error"] = "internal_error"
                t["result"] = None
                t["progress"] = 1.0
        billing.cancel_pending(apikey, "contract", task_id)
        Path(file_path).unlink(missing_ok=True)
        return

    with _lock:
        t = _tasks.get(task_id)
        cancelled = t is not None and t["status"] == "cancelled"
    if cancelled:
        # stop 已取消:跳过 commit、不覆写状态(保留 cancelled),仅释放临时文件
        logger.info("service=%s event=task_cancelled task_id=%s request_id=%s",
                    _SERVICE, task_id, request_id)
        Path(file_path).unlink(missing_ok=True)
        return

    if result["error"]:
        billing.cancel_pending(apikey, "contract", task_id)
        logger.info("service=%s event=billing_cancel task_id=%s request_id=%s error=%s",
                    _SERVICE, task_id, request_id, result["error"])
        with _lock:
            t["status"] = "failed"
            t["error"] = result["error"]
            t["result"] = result
            t["progress"] = 1.0
        Path(file_path).unlink(missing_ok=True)
        return

    try:
        billing.commit(apikey, "contract", task_id)
    except (RuntimeError, HTTPException) as exc:
        # commit 的 404(HTTPException)在事务外前置 SELECT 抛出,不再被
        # common/db.transaction 吞成 RuntimeError:必须在此捕获,否则守护线程静默死、
        # 任务卡 running、pending 泄漏、临时文件不删(终审 I1 回归)。
        # RuntimeError 仍保留(事务内 UPDATE 0 行 / apikey 不存在 的竞态兜底)。
        # 统一走 failed + cancel_pending + unlink,与文档化"任何异常兜底"意图一致。
        # ⚠️ 只记 error_type、error 字段用通用码,绝不记 str(exc):billing.commit 的
        # 异常消息可能含明文 apikey(如 "apikey xxx 不存在"),直落日志或
        # error 字段即泄露凭据(审查 Important)。
        logger.error("service=%s event=billing_commit_failed task_id=%s request_id=%s error_type=%s",
                     _SERVICE, task_id, request_id, type(exc).__name__)
        billing.cancel_pending(apikey, "contract", task_id)
        with _lock:
            t["status"] = "failed"
            t["error"] = "billing_commit_failed"
            t["result"] = None
            t["progress"] = 1.0
        Path(file_path).unlink(missing_ok=True)
        return

    logger.info("service=%s event=billing_commit task_id=%s request_id=%s",
                _SERVICE, task_id, request_id)
    with _lock:
        t["status"] = "done"
        t["error"] = ""
        t["result"] = result
        t["progress"] = 1.0
    Path(file_path).unlink(missing_ok=True)


@app.post("/api/v1/contract/review")
async def review(apikey: str = Header(...), contract_type: str = Form(...),
                 prompt: str = Form(...), file: UploadFile = File(...)):
    """提交合同审核:校验 apikey/额度/类型/大小 → 建 pending → 后台线程跑审核 → SSE 进度。"""
    request_id = uuid.uuid4().hex[:16]
    _require_key(apikey)
    billing.check_quota(apikey, "contract")
    suffix = Path(file.filename or "x.docx").suffix.lower()
    if suffix not in (".docx", ".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 docx/pdf")
    # 大小校验在写盘前(防公网 DoS):优先 UploadFile.size,再按固定上限读一次兜底
    if getattr(file, "size", None) is not None and file.size > _MAX_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 2MB 限制")
    data = await file.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 2MB 限制")
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with open(fd, "wb") as f:
            f.write(data)
        task_id = uuid.uuid4().hex
        billing.create_pending(apikey, "contract", task_id)
        with _lock:
            _tasks[task_id] = {"status": "running", "progress": 0.0,
                               "stage": "提交", "stage_title": "",
                               "result": None, "error": "", "apikey": apikey,
                               "request_id": request_id}
        logger.info("service=%s event=task_created task_id=%s request_id=%s "
                    "contract_type=%s file_size=%s",
                    _SERVICE, task_id, request_id, contract_type, len(data))
    except Exception:
        # 线程启动前异常(写盘/create_pending 429)必须删临时文件,防泄漏
        Path(tmp).unlink(missing_ok=True)
        raise
    threading.Thread(target=_run_task,
                     args=(task_id, tmp, contract_type, prompt, apikey, request_id),
                     daemon=True).start()

    def gen():
        yield {"event": "started", "data": task_id}
        while True:
            with _lock:
                t = _tasks.get(task_id)
            if t is None:
                break
            yield {"event": "progress", "data": json.dumps({
                "progress": t["progress"],
                "stage": t.get("stage", ""),
                "title": t.get("stage_title", ""),
                "current": t.get("current", 0),
                "total": t.get("total", 0),
            }, ensure_ascii=False)}
            if t["status"] in ("done", "failed", "cancelled"):
                yield {"event": t["status"], "data": str(t["error"] or "")}
                break
            time.sleep(0.5)

    return EventSourceResponse(gen(), headers={"X-Task-Id": task_id})


@app.get("/api/v1/contract/status")
def status(task_id: str, apikey: str = Header(...)):
    """任务状态 + 进度 + 阶段(仅本人任务)。"""
    _require_key(apikey)
    t = _task_for(task_id, apikey)
    return {"task_id": task_id, "status": t["status"], "progress": t["progress"],
            "stage": t.get("stage", ""), "stage_title": t.get("stage_title", "")}


@app.get("/api/v1/contract/result")
def result(task_id: str, apikey: str = Header(...)):
    """最终审核结果(JSON + markdown 报告,仅本人任务)。"""
    _require_key(apikey)
    t = _task_for(task_id, apikey)
    if t["status"] not in ("done", "failed"):
        raise HTTPException(status_code=409, detail="任务未完成")
    return {"task_id": task_id, "status": t["status"],
            "result": t["result"] or {"error": t["error"]}}


@app.post("/api/v1/contract/stop")
def stop(task_id: str, apikey: str = Header(...)):
    """停止任务:cancel_pending 释放并发额度,不扣费(仅本人任务,已终态拒绝)。"""
    _require_key(apikey)
    t = _task_for(task_id, apikey)
    if t["status"] in ("done", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail="任务已结束")
    with _lock:
        t["status"] = "cancelled"
    billing.cancel_pending(apikey, "contract", task_id)
    logger.info("service=%s event=task_stopped task_id=%s request_id=%s",
                _SERVICE, task_id, t.get("request_id", ""))
    return {"ok": True}


@app.post("/api/v1/apikeys")
def api_create(name: str = Form(...), admin: str = Header(...)):
    """创建 apikey(默认免费 10 / 付费 0),仅管理员。"""
    from common.apikey_mgmt import create_apikey
    _require_admin(admin)
    return create_apikey("contract", name)


@app.get("/api/v1/apikeys")
def api_list(admin: str = Header(...)):
    """apikey 额度使用列表,仅管理员。"""
    from common.apikey_mgmt import admin_list
    _require_admin(admin)
    return {"apikeys": admin_list(admin, "contract")}


@app.delete("/api/v1/apikeys/{apikey}")
def api_delete(apikey: str, admin: str = Header(...)):
    """停用 apikey(软删),仅管理员;不可停用自己(守卫在 apikey_mgmt)。"""
    from common.apikey_mgmt import deactivate_apikey
    _require_admin(admin)
    deactivate_apikey("contract", apikey, admin)
    return {"ok": True}
