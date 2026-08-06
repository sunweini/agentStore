"""FastAPI 接口:提交/进度/方案/勾选/入库/导出。

设计见 docs/superpowers/specs/2026-08-06-agent1-sentiment-query-agent-design.md §7/§8。

鉴权:所有接口需 Bearer apikey(auth.authenticate 依赖)。
归属:所有 /groups/{id}/* 校验 owner(auth.assert_owner)。
计费:创建记 pending,commit 转正式(billing)。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agents.agent1 import auth, billing
from agents.agent1.agent import run_pipeline
from agents.agent1.graph.state import STATUS_COMMITTED, STATUS_GENERATING, STATUS_REVIEW
from agents.agent1.store import converter, scheme_store
from common.otel import init_otel, get_tracer

app = FastAPI(title="海外舆情检索方案生成 Agent", version="0.1.0")


def _user(request: Request) -> str:
    return auth.authenticate(request)


class CreateGroupRequest(BaseModel):
    company_name: str
    role: str = "ai判定"        # 承包商/业主/ai判定
    regions: list[str] = []     # 重点地区,空 = AI 推断
    query_types: list[str] = [] # 检索类型(实体全量/负面精准/不点名/招标/快讯/司法),空 = 全部


class SelectionRequest(BaseModel):
    schemes: dict[str, bool]              # scheme_id → selected
    tracks: dict[str, dict[str, bool]]    # scheme_id → {track_key → selected}


@app.on_event("startup")
async def _startup() -> None:
    init_otel()
    # 后台任务表:group_id → asyncio.Task
    app.state.tasks = {}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/v1/groups")
async def create_group(req: CreateGroupRequest, user: str = Depends(_user)):
    """提交任务:创建方案组,后台跑 6 步流水线。"""
    if not req.company_name.strip():
        raise HTTPException(status_code=400, detail="company_name 必填")
    group_id = uuid.uuid4().hex[:16]
    billing.create_pending(user, group_id)  # 计费:记 pending,commit 转正式

    meta = {
        "role": req.role,
        "regions": req.regions,
        "query_types": req.query_types,
    }

    async def _runner():
        group = await run_pipeline(group_id, req.company_name, user, meta)
        scheme_store.save_draft(group)  # 完成后落草稿(未 commit)

    task = asyncio.create_task(_runner())
    app.state.tasks[group_id] = task
    return {"group_id": group_id, "status": STATUS_GENERATING}


@app.get("/api/v1/groups/{group_id}/progress")
async def get_progress(group_id: str, user: str = Depends(_user)):
    """查 6 步进度(每步状态/产物)。"""
    group = scheme_store.load_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="方案组不存在")
    auth.assert_owner(user, group)
    return {
        "group_id": group_id,
        "status": group["status"],
        "step_status": group.get("step_status", []),
    }


@app.get("/api/v1/groups/{group_id}/schemes")
async def get_schemes(group_id: str, user: str = Depends(_user)):
    """获取方案组(方案/轨/检索式/勾选态)。"""
    group = scheme_store.load_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="方案组不存在")
    auth.assert_owner(user, group)
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
    auth.assert_owner(user, group)
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
    auth.assert_owner(user, group)
    if group["status"] == STATUS_COMMITTED:
        raise HTTPException(status_code=409, detail="已入库")
    group["status"] = STATUS_COMMITTED
    scheme_store.save_committed(group)
    billing.commit(user, group_id)
    return {"group_id": group_id, "status": STATUS_COMMITTED}


@app.get("/api/v1/groups/{group_id}/export")
async def export_group(group_id: str, user: str = Depends(_user)):
    """导出 Excel(勾选后的轨 → spec → build_task_xlsx.py)。"""
    group = scheme_store.load_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="方案组不存在")
    auth.assert_owner(user, group)
    out = f"/tmp/{group_id}_tasks.xlsx"
    converter.export_excel(group, out)
    return FileResponse(out, filename=f"{group_id}_tasks.xlsx")
