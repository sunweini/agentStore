"""合同审核 agent FastAPI 接口。架构见 design doc §7。

接口清单:
  POST /api/v1/contract/review   上传文件 + contract_type + 审核要求 → task_id(SSE 章节进度)
  GET  /api/v1/contract/status   任务状态(解析中/审核中/完成/失败 + 进度)
  GET  /api/v1/contract/result   最终报告(JSON + markdown)
  POST /api/v1/contract/stop     停止任务(复用 sentiment stop 模式)
  POST /api/v1/contract/prompt   F1:合同类型 + 原始 prompt → 优化后 prompt
  POST /api/v1/laws/upload       用户补充法条库(管理员)
  GET  /api/v1/laws              法条库列表(law_name/条数/版本)
  POST /api/v1/apikeys           独立 apikey 创建(管理员)
  GET  /api/v1/apikeys           独立 apikey 列表(管理员)
  DELETE /api/v1/apikeys/{apikey} 独立 apikey 停用(管理员)
  GET  /health                   健康检查

鉴权/计费走 auth.py / billing.py(独立 apikey 体系,contract 独立表,与 sentiment 隔离):
  所有接口需 Header apikey;apikey 管理接口需管理员。
  审核完成 commit 扣 1 单位;F1 / 法条查询不计费;并发 pending 上限 5。

文件存 tempfile 临时目录,审核完成/失败后删除。
"""
from __future__ import annotations

import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, UploadFile, HTTPException
from sse_starlette.sse import EventSourceResponse

from agents.contract_review_agent import billing, auth
from agents.contract_review_agent.store.law_store import LawStore

app = FastAPI(title="contract_review_agent")
_law_store = LawStore(data_dir=Path("data/contract-rag"))
_tasks: dict[str, dict] = {}  # task_id -> {status, progress, result, error}
_lock = threading.Lock()


@app.get("/health")
def health():
    return {"status": "ok"}


def _require_key(apikey: str) -> dict:
    return auth.check_apikey(apikey)


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
    auth.require_admin(apikey)
    content = file.file.read().decode("utf-8", errors="replace")
    return _law_store.seed(content)


@app.get("/api/v1/laws")
def laws_list(apikey: str = Header(...)):
    """法条库列表(law_name/领域/条数)。"""
    _require_key(apikey)
    return {"laws": _law_store.list_laws()}


def _run_task(task_id: str, file_path: str, contract_type: str,
              prompt: str, apikey: str) -> None:
    """后台线程:跑完整审核流水线 → 更新任务状态 → commit/cancel 计费 → 删临时文件。"""
    from agents.contract_review_agent.agent import run_review
    result = run_review(file_path, contract_type, prompt, law_store=_law_store)
    with _lock:
        t = _tasks[task_id]
        t["status"] = "done" if not result["error"] else "failed"
        t["error"] = result["error"]
        t["result"] = result
        t["progress"] = 1.0
    if not result["error"]:
        try:
            billing.commit(apikey, task_id)
        except RuntimeError as exc:
            # commit 事务内 HTTPException(如 pending 不存在)被 common/db.transaction
            # 吞为 RuntimeError:转失败态,避免 pending 悬挂、错误静默(低优先级兜底)
            with _lock:
                _tasks[task_id]["status"] = "failed"
                _tasks[task_id]["error"] = f"commit_failed: {exc}"
    else:
        billing.cancel_pending(apikey, task_id)
    Path(file_path).unlink(missing_ok=True)


@app.post("/api/v1/contract/review")
async def review(apikey: str = Header(...), contract_type: str = Form(...),
                 prompt: str = Form(...), file: UploadFile = File(...)):
    """提交合同审核:校验 apikey/额度/类型 → 建 pending → 后台线程跑审核 → SSE 进度。"""
    key = _require_key(apikey)
    billing.check_quota(apikey)
    suffix = Path(file.filename or "x.docx").suffix.lower()
    if suffix not in (".docx", ".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 docx/pdf")
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    with open(fd, "wb") as f:
        f.write(await file.read())
    task_id = uuid.uuid4().hex
    billing.create_pending(apikey, task_id)
    with _lock:
        _tasks[task_id] = {"status": "running", "progress": 0.0,
                           "result": None, "error": "", "apikey": apikey}
    threading.Thread(target=_run_task,
                     args=(task_id, tmp, contract_type, prompt, apikey),
                     daemon=True).start()

    def gen():
        yield {"event": "started", "data": task_id}
        while True:
            with _lock:
                t = _tasks.get(task_id)
            if t is None:
                break
            yield {"event": "progress", "data": str(t["progress"])}
            if t["status"] in ("done", "failed"):
                yield {"event": t["status"], "data": str(t["error"] or "")}
                break
            time.sleep(0.5)

    return EventSourceResponse(gen())


@app.get("/api/v1/contract/status")
def status(task_id: str, apikey: str = Header(...)):
    """任务状态 + 进度。"""
    _require_key(apikey)
    with _lock:
        t = _tasks.get(task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"task_id": task_id, "status": t["status"], "progress": t["progress"]}


@app.get("/api/v1/contract/result")
def result(task_id: str, apikey: str = Header(...)):
    """最终审核结果(JSON + markdown 报告)。"""
    _require_key(apikey)
    with _lock:
        t = _tasks.get(task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if t["status"] not in ("done", "failed"):
        raise HTTPException(status_code=409, detail="任务未完成")
    return {"task_id": task_id, "status": t["status"],
            "result": t["result"] or {"error": t["error"]}}


@app.post("/api/v1/contract/stop")
def stop(task_id: str, apikey: str = Header(...)):
    """停止任务:cancel_pending 释放并发额度,不扣费。"""
    _require_key(apikey)
    with _lock:
        t = _tasks.get(task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    t["status"] = "cancelled"
    billing.cancel_pending(apikey, task_id)
    return {"ok": True}


@app.post("/api/v1/apikeys")
def api_create(name: str = Form(...), admin: str = Header(...)):
    """创建 apikey(默认免费 10 / 付费 0),仅管理员。"""
    from agents.contract_review_agent.apikey_mgmt import create_apikey
    auth.require_admin(admin)
    return create_apikey(name)


@app.get("/api/v1/apikeys")
def api_list(admin: str = Header(...)):
    """apikey 额度使用列表,仅管理员。"""
    from agents.contract_review_agent.apikey_mgmt import admin_list
    auth.require_admin(admin)
    return {"apikeys": admin_list(admin)}


@app.delete("/api/v1/apikeys/{apikey}")
def api_delete(apikey: str, admin: str = Header(...)):
    """停用 apikey(软删),仅管理员;不可停用自己(守卫在 apikey_mgmt)。"""
    from agents.contract_review_agent.apikey_mgmt import deactivate_apikey
    auth.require_admin(admin)
    deactivate_apikey(apikey, admin)
    return {"ok": True}
