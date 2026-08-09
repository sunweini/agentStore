"""编译 HTTP 服务。

请求流:
  client ──► /health 探测 ──► 200 → 提交 /compile ──► backend.compile ──► 结果 JSON
  后端不可用(如容器未起)──► CompileUnavailableError ──► 503(不算编译轮次)
  /compile 成功且后端产出 DLL(dll_path 非空)→ client 经 GET /dll/<project_name>
  拉取编译产物(冒烟链路结构级修复:编译产物在服务端留存,客户端取到本地再冒烟/打包)。

启动:
  本地/测试 ──► create_app(MockCompiler())
  容器 ──► uvicorn compile_service.server:create_factory(按 COMPILE_SERVICE_REQUIRES_DLLS 选真实/ mock 后端)
"""
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from compile_service.backends.mock import MockCompiler
from compile_service.backends.msbuild import MsbuildCompiler
from compile_service.backends.protocol import CompilerBackend
from compile_service.models import CompileFile, CompileUnavailableError, resolved_files


class CompileRequest(BaseModel):
    """编译请求:files(多文件,新)与 code(单文件,旧)二选一。

    - files: [{name, code}, ...] 多文件项目编译(每个 name 需过 _FILE_NAME_RE 白名单)
    - code:  旧单文件形态,等价 files=[{name: "Plugin.cs", code}]
    两者都空 → 400;files 存在时 code 可缺省。
    """
    files: list[CompileFile] | None = None
    code: str | None = None
    project_name: str


#: project_name 白名单(与 ArtifactStore 同源,防 DLL 下载路径穿越)
_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

#: 源文件名白名单:仅叶子名(无目录分隔符 → 防路径穿越写 tmp 之外/覆盖 csproj),
#: 首字符字母数字下划线(防 - 开头被当 msbuild 开关),仅 .cs 扩展(防 csproj 注入)
_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\.cs$")

#: REFS_DIR 缺省:代码相对 compile_service/build/references(Windows 原生部署与容器内 /app 挂载均可用;
#: 容器镜像 Dockerfile 显式 ENV REFS_DIR=/app/references 保持容器布局不变)
_DEFAULT_REFS_DIR = Path(__file__).resolve().parent / "build" / "references"


def create_app(backend) -> FastAPI:
    app = FastAPI(title="kingdee-compile-service")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/compile")
    def compile_endpoint(req: CompileRequest):
        # project_name 白名单(与 GET /dll 同源):真实后端把它拼进
        # artifact_dir/<project_name>/Plugin.dll 并 mkdir(parents=True),
        # 不校验 = 写侧路径穿越(如 ../../references 可往任意目录写文件)。
        if not _PROJECT_NAME_RE.match(req.project_name):
            raise HTTPException(400, f"非法 project_name: {req.project_name!r}")
        if not req.files and not req.code:
            raise HTTPException(400, "files 与 code 至少提供一个")
        files = resolved_files(req)
        # 文件名校验:白名单(防 ../ 路径穿越、防 - 开头开关注入、防非 .cs 覆盖 csproj)+ 去重
        seen: set[str] = set()
        for f in files:
            if not _FILE_NAME_RE.match(f.name):
                raise HTTPException(400, f"非法文件名: {f.name!r}")
            if f.name in seen:
                raise HTTPException(400, f"重复文件名: {f.name!r}")
            seen.add(f.name)
        try:
            result = backend.compile(files=files, project_name=req.project_name)
        except CompileUnavailableError as e:
            return JSONResponse(status_code=503, content={"detail": str(e)})
        return {
            "success": result.success,
            "raw_output": result.raw_output,
            "duration_ms": result.duration_ms,
            "dll_path": result.dll_path,   # 服务端留存路径(空 = 无 DLL 产出,如 mock 后端)
            "errors": [
                {"file": err.file, "line": err.line, "code": err.code, "message": err.message, "is_fatal": err.is_fatal}
                for err in result.errors
            ],
        }

    @app.get("/dll/{project_name}")
    def get_dll(project_name: str):
        """拉取编译产物 DLL(客户端把 dll_path 取到本地,供 w5.5 冒烟 / w6 打包)。"""
        if not _PROJECT_NAME_RE.match(project_name):
            raise HTTPException(400, f"非法 project_name: {project_name!r}")
        dll = getattr(backend, "artifact_dir", None)
        if dll is None:
            raise HTTPException(404, "后端未配置 DLL 留存(仅真实 msbuild 后端产出)")
        p = Path(dll) / project_name / "Plugin.dll"
        if not p.exists():
            raise HTTPException(404, f"DLL 不存在: {p}")
        return Response(content=p.read_bytes(), media_type="application/octet-stream")

    return app


def _backend_from_env() -> CompilerBackend:
    """按环境变量选后端:COMPILE_SERVICE_REQUIRES_DLLS=1 → 真实 msbuild(缺 DLL 构造即抛),否则 mock。

    环境变量(全部可选,缺省均代码相对,零硬编码部署路径):
      REFS_DIR               金蝶 BOS DLL 目录(缺省 compile_service/build/references,代码相对)
      TARGET_FRAMEWORK       编译目标(默认 v4.8,需 Developer Pack 参考程序集)
      MSBUILD_PATH           显式 msbuild 路径(缺省走 default_msbuild_path() 探测:
                             PATH 的 msbuild(VS 环境)→ Framework 自带兜底,兼容无 VS 环境)
      COMPILE_ARTIFACT_DIR   编译产物 DLL 留存目录(缺省 MsbuildCompiler 代码相对默认 仓库根/data/kingdee-compiled)
      CSC_TOOL_PATH          Roslyn csc 目录(Framework csc 仅 C# 5;真实代码用 $ 插值等语法时必配)
    """
    if os.getenv("COMPILE_SERVICE_REQUIRES_DLLS") == "1":
        # 从 REFS_DIR 目录 glob *.dll(此前只读 REFERENCE_DLLS 环境变量,容器内从未设置 → 真实后端永远无法启动)。
        # 目录缺失/为空 → glob 得空列表 → MsbuildCompiler 构造抛 CompileUnavailableError(设计行为,标记"DLL 未到位")。
        refs_dir = Path(os.getenv("REFS_DIR") or _DEFAULT_REFS_DIR)
        reference_dlls = [p for p in refs_dir.glob("*.dll")]
        artifact_dir = os.getenv("COMPILE_ARTIFACT_DIR")
        return MsbuildCompiler(
            msbuild_path=os.getenv("MSBUILD_PATH") or None,
            reference_dlls=reference_dlls,
            target_framework=os.getenv("TARGET_FRAMEWORK", "v4.8"),
            artifact_dir=Path(artifact_dir) if artifact_dir else None,
            csc_tool_path=os.getenv("CSC_TOOL_PATH") or None,
        )
    return MockCompiler()


def create_factory() -> FastAPI:
    """uvicorn 入口(Dockerfile CMD: compile_service.server:create_factory),按环境变量选真实/ mock 后端。"""
    return create_app(_backend_from_env())
