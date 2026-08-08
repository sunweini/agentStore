"""编译 HTTP 服务。

请求流:
  client ──► /health 探测 ──► 200 → 提交 /compile ──► backend.compile ──► 结果 JSON
  后端不可用(如容器未起)──► CompileUnavailableError ──► 503(不算编译轮次)
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class CompileUnavailableError(RuntimeError):
    """编译服务不可用(容器挂/超时)。与编译失败区分:前者不算编译轮次。"""


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
                {"file": e.file, "line": e.line, "code": e.code, "message": e.message, "is_fatal": e.is_fatal}
                for e in result.errors
            ],
        }

    return app
