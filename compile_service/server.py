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

from compile_service.backends.mock import MockCompiler
from compile_service.backends.msbuild import MsbuildCompiler
from compile_service.backends.protocol import CompilerBackend
from compile_service.models import CompileUnavailableError


class CompileRequest(BaseModel):
    code: str
    project_name: str


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
        # 从 REFS_DIR 目录 glob *.dll(此前只读 REFERENCE_DLLS 环境变量,容器内从未设置 → 真实后端永远无法启动)。
        # 目录缺失/为空 → glob 得空列表 → MsbuildCompiler 构造抛 CompileUnavailableError(设计行为,标记"DLL 未到位")。
        refs_dir = Path(os.getenv("REFS_DIR", "/app/references"))
        reference_dlls = [p for p in refs_dir.glob("*.dll")]
        return MsbuildCompiler(
            msbuild_path=os.getenv("MSBUILD_PATH", "msbuild"),
            reference_dlls=reference_dlls,
        )
    return MockCompiler()


def create_factory() -> FastAPI:
    """uvicorn 入口(Dockerfile CMD: compile_service.server:create_factory),按环境变量选真实/ mock 后端。"""
    return create_app(_backend_from_env())
