"""编译 HTTP 服务。

请求流:
  client ──► /health 探测 ──► 200 → 提交 /compile ──► backend.compile ──► 结果 JSON
  后端不可用(如容器未起)──► CompileUnavailableError ──► 503(不算编译轮次)

启动:
  本地/测试 ──► create_app(MockCompiler())
  容器 ──► uvicorn compile_service.server:create_factory(按 COMPILE_SERVICE_REQUIRES_DLLS 选真实/ mock 后端)
"""
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class CompileUnavailableError(RuntimeError):
    """编译服务不可用(容器挂/超时)。与编译失败区分:前者不算编译轮次。"""


class CompileRequest(BaseModel):
    code: str
    project_name: str


# 注意:后端导入必须放在 CompileUnavailableError 定义之后——msbuild 后端反向依赖本模块的该异常,
# 若放文件顶部会触发循环导入(partially initialized module)。
from compile_service.backends.mock import MockCompiler
from compile_service.backends.msbuild import MsbuildCompiler
from compile_service.backends.protocol import CompilerBackend


def create_app(backend) -> FastAPI:
    app = FastAPI(title="kingdee-compile-service")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/compile")
    def compile_endpoint(req: CompileRequest):
        try:
            result = backend.compile(code=req.code, project_name=req.project_name)
        except CompileUnavailableError as e:
            return JSONResponse(status_code=503, content={"detail": str(e)})
        return {
            "success": result.success,
            "raw_output": result.raw_output,
            "duration_ms": result.duration_ms,
            "errors": [
                {"file": err.file, "line": err.line, "code": err.code, "message": err.message, "is_fatal": err.is_fatal}
                for err in result.errors
            ],
        }

    return app


def _backend_from_env() -> CompilerBackend:
    """按环境变量选后端:COMPILE_SERVICE_REQUIRES_DLLS=1 → 真实 msbuild(缺 DLL 构造即抛),否则 mock。"""
    if os.getenv("COMPILE_SERVICE_REQUIRES_DLLS") == "1":
        return MsbuildCompiler(
            msbuild_path=os.getenv("MSBUILD_PATH", "msbuild"),
            reference_dlls=[Path(p) for p in os.getenv("REFERENCE_DLLS", "").split(os.pathsep) if p],
        )
    return MockCompiler()


def create_factory() -> FastAPI:
    """uvicorn 入口(Dockerfile CMD: compile_service.server:create_factory),按环境变量选真实/ mock 后端。"""
    return create_app(_backend_from_env())
