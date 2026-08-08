"""编译服务 HTTP 客户端(真实/mock 无感切换:同一 base_url 契约)。

DLL 产物(冒烟链路结构级修复):/compile 成功且服务端产出 DLL(响应 dll_path
非空)→ 客户端经 `GET /dll/{project_name}` 把编译产物拉到本地
(artifact_dir/<project_name>/Plugin.dll),CompileResult.dll_path 返回**本地
路径** —— w5.5 冒烟 / w6 打包直接可用;拉取失败 → dll_path 置空(优雅降级,
冒烟侧按"无 DLL"跳过验证)。mock 后端无产出 → 响应 dll_path 为空,不走拉取。
"""
import os
from pathlib import Path

import httpx
from compile_service.models import CompileError, CompileResult, CompileUnavailableError


class CompileClient:
    #: 单轮编译 ≤2min(设计 §6.6),timeout 按上限定;10s 会让真实编译超时误判
    def __init__(self, base_url: str, timeout: float = 120.0,
                 artifact_dir: Path = Path("data/kingdee-compiled")):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.artifact_dir = Path(artifact_dir)
        self.session = httpx.Client(timeout=timeout)

    def health(self) -> bool:
        try:
            r = self.session.get(f"{self.base_url}/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def _fetch_dll(self, project_name: str) -> str:
        """服务端留存的编译产物 → 本地 artifact_dir(返回本地路径;失败返回空)。"""
        try:
            r = self.session.get(f"{self.base_url}/dll/{project_name}")
            if r.status_code != 200:
                return ""
            target = self.artifact_dir / project_name / "Plugin.dll"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(r.content)
            return str(target)
        except (httpx.HTTPError, OSError):
            # 拉取/落盘失败(网络、磁盘满、权限等)→ 无本地 DLL,冒烟按
            # "无 DLL"跳过验证(优雅降级,不把 w5 节点打崩)
            return ""

    def compile(self, code: str, project_name: str) -> CompileResult:
        r = self.session.post(f"{self.base_url}/compile",
                              json={"code": code, "project_name": project_name})
        if r.status_code == 503:
            raise CompileUnavailableError(r.json().get("detail", "compiler unavailable"))
        data = r.json()
        result = CompileResult(
            success=data["success"], raw_output=data.get("raw_output", ""),
            duration_ms=data.get("duration_ms", 0),
            errors=[CompileError(**e) for e in data.get("errors", [])])
        if result.success and data.get("dll_path"):
            result.dll_path = self._fetch_dll(project_name)
        return result


def compile_client_from_env() -> CompileClient:
    return CompileClient(base_url=os.getenv("COMPILE_SERVICE_URL", "http://localhost:8000"))
