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
from pydantic import BaseModel, Field

from common import apikey_mgmt, auth, billing, config

logger = logging.getLogger(__name__)

# ADMIN_APIKEY 未配置时 console 锁死(超管接口一律 403),启动即告警便于运维定位
if not config.get_env("ADMIN_APIKEY"):
    logger.warning("service=admin_console component=admin_api event=admin_apikey_unset "
                   "message=ADMIN_APIKEY 未配置,管理控制台接口将全部 403 拒绝")

app = FastAPI(title="agentStore 管理控制台", version="0.1.0")

# CORS:admin.html 由本服务同源 FileResponse 提供,浏览器同源请求不触发 CORS 预检,
# 无需放开跨域。默认空(不跨域),仅当显式配 CORS_ORIGINS 才放开 —— 收紧默认,
# 避免超级管理员凭据管理面被任意跨域站点访问。
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins,
                   allow_methods=["*"], allow_headers=["*"])

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _require_super_admin(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    if not auth.is_super_admin(token):
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    return token


class CreateApiKeyRequest(BaseModel):
    agent: str = Field(min_length=1, max_length=64)
    role: str = "normal"
    # 负值不加 ge=0:须在 handler 内走 create_apikey 的 ValueError → 400(维持 400 语义,
    # pydantic 的 ge 会返回 422 破坏既有测试)
    free_quota: int | None = None
    paid_quota: int | None = None


class SetRoleRequest(BaseModel):
    apikey: str = Field(min_length=1, max_length=128)
    agent: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=10)


class UpdateApiKeyRequest(BaseModel):
    apikey: str = Field(min_length=1, max_length=128)
    agent: str = Field(min_length=1, max_length=64)
    new_apikey: str = Field(min_length=1, max_length=128)


class DeleteApiKeyRequest(BaseModel):
    apikey: str = Field(min_length=1, max_length=128)
    agent: str = Field(min_length=1, max_length=64)


class QuotaRequest(BaseModel):
    apikey: str = Field(min_length=1, max_length=128)
    agent: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=10)
    # count 下界走 handler 手动校验(400),上界 le 防超 MySQL INT 上限 500
    count: int = Field(le=1000000000)


@app.get("/api/v1/admin/agents")
async def list_agents_api(admin: str = Depends(_require_super_admin)):
    return {"agents": apikey_mgmt.list_agents()}


@app.get("/api/v1/admin/apikeys")
async def list_keys_api(agent: str | None = None, admin: str = Depends(_require_super_admin)):
    return {"keys": apikey_mgmt.list_keys(agent=agent)}


@app.post("/api/v1/admin/apikeys")
async def create_apikey_api(req: CreateApiKeyRequest, admin: str = Depends(_require_super_admin)):
    try:
        key = apikey_mgmt.create_apikey(
            req.agent, "", role=req.role, free_quota=req.free_quota, paid_quota=req.paid_quota)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("service=admin_console component=admin_api event=apikey_created "
                "agent=%s apikey=%s role=%s free_quota=%s paid_quota=%s",
                req.agent, apikey_mgmt._mask_apikey(key["apikey"]), key["role"],
                key["free_quota"], key["paid_quota"])
    return key


@app.patch("/api/v1/admin/apikeys")
async def set_role_api(req: SetRoleRequest, admin: str = Depends(_require_super_admin)):
    result = apikey_mgmt.set_role(req.agent, req.apikey, req.role, admin)
    logger.info("service=admin_console component=admin_api event=apikey_role_changed "
                "agent=%s apikey=%s role=%s", req.agent, apikey_mgmt._mask_apikey(req.apikey), req.role)
    return result


@app.put("/api/v1/admin/apikeys")
async def update_apikey_api(req: UpdateApiKeyRequest, admin: str = Depends(_require_super_admin)):
    result = apikey_mgmt.update_apikey(req.agent, req.apikey, req.new_apikey)
    logger.info("service=admin_console component=admin_api event=apikey_updated "
                "agent=%s apikey=%s new_apikey=%s",
                req.agent, apikey_mgmt._mask_apikey(req.apikey),
                apikey_mgmt._mask_apikey(req.new_apikey))
    return result


@app.delete("/api/v1/admin/apikeys")
async def delete_apikey_api(req: DeleteApiKeyRequest, admin: str = Depends(_require_super_admin)):
    apikey_mgmt.deactivate_apikey(req.agent, req.apikey, admin)
    logger.info("service=admin_console component=admin_api event=apikey_deleted "
                "agent=%s apikey=%s", req.agent, apikey_mgmt._mask_apikey(req.apikey))
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
    logger.info("service=admin_console component=admin_api event=apikey_quota_added "
                "agent=%s apikey=%s type=%s count=%s", req.agent,
                apikey_mgmt._mask_apikey(req.apikey), req.type, req.count)
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
