"""真实 msbuild 编译后端(容器内)。

状态:
  无 DLL ──► 构造抛 CompileUnavailableError(服务不启动,标记"DLL 未到位")
  有 DLL ──► compile: 生成 csproj+源文件 ──► msbuild ──► 输出交解析器

DLL 产物(冒烟链路结构级修复):编译成功后把输出 DLL(临时目录,编译完即删)
**复制到服务端留存目录**(artifact_dir/<project_name>/Plugin.dll,编译期唯一),
result.dll_path 返回留存路径,客户端经 `GET /dll/{project_name}` 拉取。
mock 后端无产出 → dll_path 为空。
"""
import subprocess
import tempfile
from pathlib import Path
from compile_service.backends.protocol import CompilerBackend
from compile_service.error_parser import parse_compile_output
from compile_service.models import CompileResult, CompileUnavailableError


class MsbuildCompiler(CompilerBackend):
    def __init__(self, msbuild_path: str, reference_dlls: list[Path],
                 artifact_dir: Path = Path("data/kingdee-compiled")):
        if not reference_dlls:
            raise CompileUnavailableError("金蝶 BOS DLL 未提供,真实编译不可用")
        self.msbuild_path = msbuild_path
        self.reference_dlls = reference_dlls
        self.artifact_dir = Path(artifact_dir)

    def compile(self, code: str, project_name: str) -> CompileResult:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "Plugin.cs"
            src.write_text(code, encoding="utf-8")
            csproj = Path(tmp) / "Plugin.csproj"
            refs = "".join(f'<Reference Include="{d.stem}"><HintPath>{d}</HintPath></Reference>' for d in self.reference_dlls)
            csproj.write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                f"</PropertyGroup><ItemGroup>{refs}</ItemGroup></Project>", encoding="utf-8")
            proc = subprocess.run(
                [self.msbuild_path, str(csproj), "/nologo", "/v:minimal"],
                capture_output=True, text=True, timeout=120)
            raw = (proc.stdout or "") + (proc.stderr or "")
            built_dll = next(Path(tmp).rglob("Plugin.dll"), None)
        result = parse_compile_output(raw)
        # 进程非零退出(msbuild 崩溃/引用缺失/工具链异常)即使无错误行也判失败,不能只信输出文本
        result.success = result.success and proc.returncode == 0
        result.duration_ms = 0
        if result.success and built_dll is not None:
            target = self.artifact_dir / project_name / "Plugin.dll"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(built_dll.read_bytes())
            result.dll_path = str(target)
        return result
