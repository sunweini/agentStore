"""管理控制台 API:跨 agent apikey 管理 + 报表 + 额度。超级管理员(ADMIN_APIKEY)专用。

设计见 docs/superpowers/specs/2026-08-14-admin-console-design.md。
薄层转调 common/apikey_mgmt.py / common/billing.py;所有 key 定向操作 body 传参
(key 不进 URL,防 access log 泄露凭据)。启动:`uvicorn common.admin_api:app`。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from common import apikey_mgmt, auth, billing

logger = logging.getLogger(__name__)

app = FastAPI(title="agentStore 管理控制台", version="0.1.0")

_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins,
                   allow_methods=["*"], allow_headers=["*"])

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _require_super_admin(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    if not auth.is_super_admin(token):
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    return token


class CreateApiKeyRequest(BaseModel):
    agent: str
    role: str = "normal"
    free_quota: int | None = None
    paid_quota: int | None = None


class SetRoleRequest(BaseModel):
    apikey: str
    agent: str
    role: str


class UpdateApiKeyRequest(BaseModel):
    apikey: str
    agent: str
    new_apikey: str


class DeleteApiKeyRequest(BaseModel):
    apikey: str
    agent: str


class QuotaRequest(BaseModel):
    apikey: str
    agent: str
    type: str
    count: int


@app.get("/api/v1/admin/agents")
async def list_agents_api(admin: str = Depends(_require_super_admin)):
    return {"agents": apikey_mgmt.list_agents()}


@app.get("/api/v1/admin/apikeys")
async def list_keys_api(agent: str | None = None, admin: str = Depends(_require_super_admin)):
    return {"keys": apikey_mgmt.list_keys(agent=agent)}


@app.post("/api/v1/admin/apikeys")
async def create_apikey_api(req: CreateApiKeyRequest, admin: str = Depends(_require_super_admin)):
    try:
        return apikey_mgmt.create_apikey(
            req.agent, "", role=req.role, free_quota=req.free_quota, paid_quota=req.paid_quota)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/v1/admin/apikeys")
async def set_role_api(req: SetRoleRequest, admin: str = Depends(_require_super_admin)):
    return apikey_mgmt.set_role(req.agent, req.apikey, req.role, admin)


@app.put("/api/v1/admin/apikeys")
async def update_apikey_api(req: UpdateApiKeyRequest, admin: str = Depends(_require_super_admin)):
    return apikey_mgmt.update_apikey(req.agent, req.apikey, req.new_apikey)


@app.delete("/api/v1/admin/apikeys")
async def delete_apikey_api(req: DeleteApiKeyRequest, admin: str = Depends(_require_super_admin)):
    apikey_mgmt.deactivate_apikey(req.agent, req.apikey, admin)
    return {"apikey": req.apikey, "agent": req.agent, "deleted": True}


@app.post("/api/v1/admin/apikeys/quota")
async def add_quota_api(req: QuotaRequest, admin: str = Depends(_require_super_admin)):
    if req.count <= 0:
        raise HTTPException(status_code=400, detail="count 必须为正数")
    if req.type == "free":
        billing.add_free_quota(req.apikey, req.agent, req.count)
    elif req.type == "paid":
        billing.add_paid_quota(req.apikey, req.agent, req.count)
    else:
        raise HTTPException(status_code=400, detail="type 必须为 free 或 paid")
    return {"apikey": req.apikey, "agent": req.agent, "type": req.type, "added": req.count}


@app.get("/api/v1/admin/report/summary")
async def report_summary_api(agent: str | None = None, admin: str = Depends(_require_super_admin)):
    return billing.report_summary(agent=agent)


@app.get("/api/v1/admin/report/history")
async def report_history_api(agent: str | None = None, days: int = 30,
                             admin: str = Depends(_require_super_admin)):
    if not 1 <= days <= 365:
        raise HTTPException(status_code=400, detail="days 必须在 1-365")
    return billing.report_history(agent=agent, days=days)


@app.get("/")
async def index():
    return FileResponse(_WEB_DIR / "admin.html")
