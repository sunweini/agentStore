"""FastAPI 接口:提交/进度/方案/勾选/入库/导出。

设计见 docs/superpowers/specs/2026-08-06-sentiment-query-agent-sentiment-query-agent-design.md §7/§8。

鉴权:所有接口需 Bearer apikey(common.auth.check_apikey 依赖,agent='sentiment')。
归属:所有 /groups/{id}/* 校验 owner(common.auth.assert_owner)。
计费:创建记 pending,commit 转正式(common.billing)。
apikey 管理走 common.apikey_mgmt(agent='sentiment');创建接口保持"调用方传 key"
语义,由本地兼容包装 _create_apikey_compat 适配(见下)。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from common import apikey_mgmt, auth, billing, db
from agents.sentiment_query_agent.agent import run_pipeline
from agents.sentiment_query_agent.graph.state import (
    STATUS_COMMITTED, STATUS_GENERATING, STATUS_REVIEW, STATUS_STOPPED,
)
from agents.sentiment_query_agent.store import converter, scheme_store
from common.otel import init_otel, get_tracer

app = FastAPI(title="海外舆情检索方案生成 Agent", version="0.1.0")

# CORS:允许前端演示页(web/demo.html)跨域访问
# 生产用 CORS_ORIGINS 收紧(逗号分隔源,如 http://10.33.17.72);缺省 "*" 兼容测试环境
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger(__name__)

_AGENT = "sentiment"  # 公共计费/鉴权组件的 agent 维度(单表收敛后按 agent 隔离额度)


def _setup_file_logging() -> None:
    """LOG_DIR 环境变量设置时日志落盘(RotatingFileHandler,10MB×5);未设置仅 stdout。"""
    log_dir = os.getenv("LOG_DIR")
    if not log_dir:
        return
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path / "api.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    # root 接业务日志(agents.*);uvicorn logger 设了 propagate=False,
    # 访问日志到不了 root,需单独挂。注意不挂 uvicorn.error:
    # 它向父 "uvicorn" 传播,挂两处会导致文件里每行重复
    for lg in (
        logging.getLogger(),
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.access"),
    ):
        lg.addHandler(handler)
    root = logging.getLogger()
    if root.level == logging.NOTSET:
        root.setLevel(logging.INFO)


def _user(request: Request) -> str:
    """Bearer apikey 鉴权依赖(agent='sentiment'):解析头 + common.auth 校验,无效 → 401。"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Authorization: Bearer <apikey>")
    apikey = auth_header[7:].strip()
    auth.check_apikey(apikey, _AGENT)
    return apikey


def _is_admin(user: str) -> bool:
    """user 是否为 sentiment 管理员(资费查询分流)。无效/非 active → False(语义同旧 auth.is_admin)。"""
    try:
        return auth.check_apikey(user, _AGENT)["role"] == "admin"
    except HTTPException:
        return False


def _create_apikey_compat(apikey: str) -> dict:
    """兼容包装:保持"调用方传 key"语义(旧 POST /apikeys 行为)。

    公共 create_apikey(agent, name, role) 为服务端随机 key,与 sentiment 现有
    接口(调用方传 apikey 字符串)签名不兼容;故在 api.py 层校验 sk- 格式后手动
    插 agent_api_keys(agent='sentiment'),返回结构与旧版一致。
    """
    if not re.fullmatch(r"sk-[A-Za-z0-9]{6,64}", apikey):
        raise HTTPException(status_code=400, detail="apikey 格式:sk- 开头 + 6-64 位字母数字")
    try:
        db.execute(
            "INSERT INTO agent_api_keys (apikey, agent, role, status, free_quota, paid_quota) "
            "VALUES (%s, %s, 'normal', 'active', 10, 0)",
            (apikey, _AGENT),
        )
    except RuntimeError as exc:
        if "Duplicate" in str(exc) or "1062" in str(exc) or "UNIQUE" in str(exc):
            raise HTTPException(status_code=409, detail="apikey 已存在") from exc
        raise
    return {"apikey": apikey, "free_quota": 10, "paid_quota": 0}


class CreateGroupRequest(BaseModel):
    company_name: str
    role: str = "ai判定"        # 承包商/业主/ai判定
    regions: list[str] = []     # 重点地区,空 = AI 推断
    query_types: list[str] = [] # 检索类型(全量新闻/负面新闻/行业新闻/招标/快讯/司法),空 = 全部


class SelectionRequest(BaseModel):
    schemes: dict[str, bool]              # scheme_id → selected
    tracks: dict[str, dict[str, bool]]    # scheme_id → {track_key → selected}


@app.on_event("startup")
async def _startup() -> None:
    _setup_file_logging()
    init_otel()
    # 后台任务表:group_id → asyncio.Task
    app.state.tasks = {}
    # 提交元信息:group_id → {owner, company_name, meta, created_at}
    # 用途:checkpoint 落盘前(提交后瞬间)的归属校验与 stop 兜底
    app.state.metas = {}
    # 确保管理员 apikey 存在(额度 99999999,幂等)
    apikey_mgmt.ensure_admin(_AGENT)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/v1/groups")
async def create_group(req: CreateGroupRequest, user: str = Depends(_user)):
    """提交任务:创建方案组,后台跑 6 步流水线。"""
    if not req.company_name.strip():
        raise HTTPException(status_code=400, detail="company_name 必填")
    group_id = uuid.uuid4().hex[:16]
    billing.check_quota(user, _AGENT)          # 额度校验:free+paid remaining > 0,否则 403
    billing.create_pending(user, _AGENT, group_id)  # 计费:记 pending,commit 转正式

    meta = {
        "role": req.role,
        "regions": req.regions,
        "query_types": req.query_types,
    }

    async def _runner():
        try:
            group = await run_pipeline(group_id, req.company_name, user, meta)
            scheme_store.save_draft(group)  # 完成后落草稿(未 commit)
        except Exception as exc:
            # 任务失败:落错误草稿,进度接口可查(不静默);取消 pending 释放并发额度
            logger.error("service=sentiment-query-agent event=pipeline_failed group_id=%s error=%s", group_id, exc)
            billing.cancel_pending(user, _AGENT, group_id)
            scheme_store.save_draft({
                "group_id": group_id, "owner": user, "company_name": req.company_name,
                "meta": meta, "status": "failed", "step_status": [],
                "schemes": [], "created_at": datetime.now().isoformat(), "committed_at": None,
                "error": str(exc),
            })

    task = asyncio.create_task(_runner())
    app.state.tasks[group_id] = task
    app.state.metas[group_id] = {
        "owner": user, "company_name": req.company_name, "meta": meta,
        "created_at": datetime.now().isoformat(),
    }
    return {"group_id": group_id, "status": STATUS_GENERATING}


@app.get("/api/v1/groups/{group_id}/progress")
async def get_progress(group_id: str, user: str = Depends(_user)):
    """查 6 步进度(每步状态/产物)。

    数据源:优先草稿文件(完成后),否则从 checkpoint 读实时进度(生成中)。
    """
    group = scheme_store.load_group(group_id)
    if group is None:
        # 生成中:从 checkpoint 读实时进度(thread_id = group_id)
        group = await _load_from_checkpoint(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="方案组不存在")
    auth.assert_owner(user, group.get("owner"), admin=user)
    return {
        "group_id": group_id,
        "status": group["status"],
        "step_status": group.get("step_status", []),
    }


@app.get("/api/v1/groups/{group_id}/status")
async def get_status(group_id: str, user: str = Depends(_user)):
    """轻量查运行状态:status + 是否后台运行 + 当前步骤,不带 step 产物。

    供前端/调用方做轮询心跳与"能否查方案组"判断:
    status=review(或 stopped/committed)且 running=false 时可查 schemes。
    """
    task = app.state.tasks.get(group_id)
    running = task is not None and not task.done()
    group = scheme_store.load_group(group_id)
    if group is None:
        group = await _load_from_checkpoint(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="方案组不存在")
    auth.assert_owner(user, group.get("owner"), admin=user)

    # 当前步骤:有 running 步取其步号,否则取已出现的最大步号
    step_status = group.get("step_status", [])
    running_steps = [s["step"] for s in step_status if s.get("status") == "running"]
    current_step = running_steps[-1] if running_steps else (
        max((s["step"] for s in step_status), default=0)
    )
    steps_done = sum(1 for s in step_status if s.get("status") == "done")
    steps_error = sum(1 for s in step_status if s.get("status") == "error")
    # 明确标识:review/committed = 6 步流程已结束,可调 schemes
    schemes_ready = group["status"] in (STATUS_REVIEW, STATUS_COMMITTED)
    return {
        "group_id": group_id,
        "status": group["status"],
        "running": running,
        "current_step": current_step,
        "total_steps": 6,
        "steps_done": steps_done,
        "steps_error": steps_error,
        "schemes_ready": schemes_ready,
    }


@app.post("/api/v1/groups/{group_id}/stop")
async def stop_group(group_id: str, user: str = Depends(_user)):
    """停止正在运行的组:取消后台任务,标记 stopped,不重启进程。

    - generating 态:取消后台任务,标记 stopped。
    - review 态(生成已完成未入库):无后台任务,直接标记 stopped(前端"重新生成"场景)。
    - committed/stopped 态:拒绝(409)。
    - 取消后落 stopped 草稿,保留已完成步骤产物。
    - 取消 pending 计费记录(stop 未 commit 不计费),释放并发额度。
    """
    task = app.state.tasks.get(group_id)
    group = scheme_store.load_group(group_id)
    if group is None:
        group = await _load_from_checkpoint(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="方案组不存在")
    auth.assert_owner(user, group.get("owner"), admin=user)

    if group["status"] not in (STATUS_GENERATING, STATUS_REVIEW):
        raise HTTPException(status_code=409, detail=f"组状态 {group['status']},仅 generating/review 可停止")

    if task is not None and not task.done():
        task.cancel()
        # 等待任务真正退出,避免取消后 runner 继续写草稿覆盖 stopped 状态
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # 取消过程中 runner 抛错,忽略(目的就是停)
            pass

    group["status"] = STATUS_STOPPED
    scheme_store.save_draft(group)
    # 取消 pending 计费记录,释放并发额度(stop 未 commit 不计费)
    billing.cancel_pending(user, _AGENT, group_id)
    logger.info("service=sentiment-query-agent event=group_stopped group_id=%s user=%s", group_id, user)
    return {"group_id": group_id, "status": STATUS_STOPPED}


async def _load_from_checkpoint(group_id: str) -> dict | None:
    """从 LangGraph checkpoint 读 group 状态(生成中进度)。"""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from agents.sentiment_query_agent.graph.flows import build_graph
    from agents.sentiment_query_agent.agent import _CHECKPOINT_DB

    try:
        async with AsyncSqliteSaver.from_conn_string(str(_CHECKPOINT_DB)) as saver:
            # WAL 与 run_pipeline 写路径一致(幂等;确保读不阻塞写)
            await saver.conn.execute("PRAGMA journal_mode=WAL")
            g = build_graph().compile(checkpointer=saver)
            state = await g.aget_state({"configurable": {"thread_id": group_id}})
            group = state.values.get("group")
            if group is None:
                return None
            # 生成中:status 仍为 generating(图内更新),补默认
            group.setdefault("status", STATUS_GENERATING)
            return group
    except Exception:
        return None


@app.get("/api/v1/groups/{group_id}/schemes")
async def get_schemes(group_id: str, user: str = Depends(_user)):
    """获取方案组(方案/轨/检索式/勾选态)。"""
    group = scheme_store.load_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="方案组不存在")
    auth.assert_owner(user, group.get("owner"), admin=user)
    return {
        "group_id": group_id,
        "company_name": group["company_name"],
        "meta": group.get("meta", {}),
        "status": group["status"],
        "schemes": group.get("schemes", []),
        "keywords": group.get("keywords", []),
    }


@app.put("/api/v1/groups/{group_id}/selection")
async def update_selection(group_id: str, req: SelectionRequest, user: str = Depends(_user)):
    """提交勾选(方案级 + 轨级)。已 commit 的组冻结,拒绝修改。"""
    group = scheme_store.load_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="方案组不存在")
    auth.assert_owner(user, group.get("owner"), admin=user)
    if group["status"] == STATUS_COMMITTED:
        raise HTTPException(status_code=409, detail="方案组已入库冻结,不可改勾选")

    for sc in group.get("schemes", []):
        if sc["id"] in req.schemes:
            sc["selected"] = req.schemes[sc["id"]]
        for tr in sc.get("tracks", []):
            sel = req.tracks.get(sc["id"], {})
            if tr["key"] in sel:
                tr["selected"] = sel[tr["key"]]
    scheme_store.save_draft(group)
    return {"group_id": group_id, "updated": True}


@app.post("/api/v1/groups/{group_id}/commit")
async def commit_group(group_id: str, user: str = Depends(_user)):
    """确认入库:固化正式文件 + 计费转正式。"""
    group = scheme_store.load_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="方案组不存在")
    auth.assert_owner(user, group.get("owner"), admin=user)
    if group["status"] == STATUS_COMMITTED:
        raise HTTPException(status_code=409, detail="已入库")
    if group["status"] != STATUS_REVIEW:
        raise HTTPException(status_code=409, detail=f"组状态 {group['status']},仅 review(待勾选)可入库")
    group["status"] = STATUS_COMMITTED
    scheme_store.save_committed(group)
    billing.commit(user, _AGENT, group_id)
    return {"group_id": group_id, "status": STATUS_COMMITTED}


@app.get("/api/v1/groups/{group_id}/export")
async def export_group(group_id: str, user: str = Depends(_user)):
    """导出 Excel(勾选后的轨 → spec → build_task_xlsx.py)。"""
    group = scheme_store.load_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="方案组不存在")
    auth.assert_owner(user, group.get("owner"), admin=user)
    out = f"/tmp/{group_id}_tasks.xlsx"
    converter.export_excel(group, out)
    return FileResponse(out, filename=f"{group_id}_tasks.xlsx")


# ===== 多用户配额与资费(2026-08-11) =====

class CreateApiKeyRequest(BaseModel):
    apikey: str


class UpdateApiKeyRequest(BaseModel):
    old_apikey: str
    new_apikey: str


class QuotaChangeRequest(BaseModel):
    apikey: str
    count: int


@app.post("/api/v1/apikeys")
async def create_apikey_api(req: CreateApiKeyRequest, user: str = Depends(_user)):
    """创建 apikey(默认免费 10/付费 0)。仅管理员。"""
    auth.require_admin(user, _AGENT)
    return _create_apikey_compat(req.apikey)


@app.put("/api/v1/apikeys")
async def update_apikey_api(req: UpdateApiKeyRequest, user: str = Depends(_user)):
    """修改 apikey:旧 key → 新 key,资费继承 + 历史迁移。仅管理员。"""
    auth.require_admin(user, _AGENT)
    return apikey_mgmt.update_apikey(_AGENT, req.old_apikey, req.new_apikey)


@app.delete("/api/v1/apikeys/{apikey}")
async def delete_apikey_api(apikey: str, user: str = Depends(_user)):
    """删除 apikey(软删,数据保留)。仅管理员。"""
    auth.require_admin(user, _AGENT)
    apikey_mgmt.deactivate_apikey(_AGENT, apikey, user)
    return {"apikey": apikey, "deleted": True}


@app.get("/api/v1/apikeys/list")
async def list_apikeys_api(user: str = Depends(_user)):
    """查所有普通用户 apikey 额度。仅管理员。"""
    auth.require_admin(user, _AGENT)
    return {"users": billing.usage_all(agent=_AGENT)}


@app.get("/api/v1/apikeys/pending")
async def pending_api(user: str = Depends(_user)):
    """查当前 apikey 的 pending 任务。本人。"""
    pending = [
        {"group_id": r["bill_no"], "created_at": r["created_at"]}
        for r in billing.list_pending(user, _AGENT)
    ]
    return {"apikey": user, "pending": pending}


@app.get("/api/v1/billing/usage")
async def billing_usage_api(user: str = Depends(_user)):
    """资费查询:普通查自己;管理员查全部。"""
    if _is_admin(user):
        return {"role": "admin", "users": billing.usage_all(agent=_AGENT)}
    return {"role": "normal", **billing.usage(user, _AGENT)}


@app.post("/api/v1/billing/quota/paid")
async def add_paid_quota_api(req: QuotaChangeRequest, user: str = Depends(_user)):
    """增加付费额度。仅管理员。"""
    auth.require_admin(user, _AGENT)
    if req.count <= 0:
        raise HTTPException(status_code=400, detail="count 必须为正数")
    billing.add_paid_quota(req.apikey, _AGENT, req.count)
    return {"apikey": req.apikey, "paid_added": req.count}


@app.post("/api/v1/billing/quota/free")
async def add_free_quota_api(req: QuotaChangeRequest, user: str = Depends(_user)):
    """增加免费额度。仅管理员。"""
    auth.require_admin(user, _AGENT)
    if req.count <= 0:
        raise HTTPException(status_code=400, detail="count 必须为正数")
    billing.add_free_quota(req.apikey, _AGENT, req.count)
    return {"apikey": req.apikey, "free_added": req.count}
