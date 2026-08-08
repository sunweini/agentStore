"""编译服务 HTTP 客户端(真实/mock 无感切换:同一 base_url 契约)。"""
import os
import httpx
from compile_service.models import CompileError, CompileResult, CompileUnavailableError


class CompileClient:
    #: 单轮编译 ≤2min(设计 §6.6),timeout 按上限定;10s 会让真实编译超时误判
    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = httpx.Client(timeout=timeout)

    def health(self) -> bool:
        try:
            r = self.session.get(f"{self.base_url}/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def compile(self, code: str, project_name: str) -> CompileResult:
        r = self.session.post(f"{self.base_url}/compile",
                              json={"code": code, "project_name": project_name})
        if r.status_code == 503:
            raise CompileUnavailableError(r.json().get("detail", "compiler unavailable"))
        data = r.json()
        return CompileResult(
            success=data["success"], raw_output=data.get("raw_output", ""),
            duration_ms=data.get("duration_ms", 0),
            errors=[CompileError(**e) for e in data.get("errors", [])])


def compile_client_from_env() -> CompileClient:
    return CompileClient(base_url=os.getenv("COMPILE_SERVICE_URL", "http://localhost:8000"))
