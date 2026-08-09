"""真实 msbuild 编译后端(容器内 / Windows 构建机)。

状态:
  无 DLL ──► 构造抛 CompileUnavailableError(服务不启动,标记"DLL 未到位")
  有 DLL ──► compile: 生成旧式 csproj + 源文件 ──► msbuild ──► 输出交解析器

兼容性:生成**旧式 csproj**(ToolsVersion 4.0),兼容无 VS 的机器上
.NET Framework 自带 MSBuild(最后兜底 `C:\\Windows\\Microsoft.NET\\Framework64\\v4.0.30319\\MSBuild.exe`,
可用 `FRAMEWORK_MSBUILD_PATH` 环境变量覆盖),
配合 .NET Framework Developer Pack(参考程序集)编译 TargetFrameworkVersion 目标。
SDK 风格 csproj 需要 VS 15+,纯 Framework 环境不可用。

DLL 产物(冒烟链路结构级修复):编译成功后把输出 DLL(临时目录,编译完即删)
**复制到服务端留存目录**(artifact_dir/<project_name>/Plugin.dll,编译期唯一),
result.dll_path 返回留存路径,客户端经 `GET /dll/{project_name}` 拉取。
mock 后端无产出 → dll_path 为空。
"""
import subprocess
import tempfile
import xml.sax.saxutils
from pathlib import Path
from compile_service.backends.protocol import CompilerBackend
from compile_service.error_parser import parse_compile_output
from compile_service.models import CompileFile, CompileResult, CompileUnavailableError

# .NET Framework 自带 MSBuild 探测路径(无 VS 环境的**最后兜底**;可用 FRAMEWORK_MSBUILD_PATH 环境变量覆盖)
_FRAMEWORK_MSBUILD = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\MSBuild.exe"

# 编译产物 DLL 留存目录缺省:代码相对(compile_service/backends/msbuild.py 上溯 3 层 = 仓库根/data/kingdee-compiled),
# 不随 cwd 漂移;构造函数 artifact_dir 或服务端 COMPILE_ARTIFACT_DIR 环境变量可覆盖
_DEFAULT_ARTIFACT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "kingdee-compiled"

# 旧式 csproj 模板:兼容 Framework MSBuild 4.0(无 VS 环境)
_CSPROJ_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<Project ToolsVersion="4.0" DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <PropertyGroup>
    <Configuration Condition=" '$(Configuration)' == '' ">Debug</Configuration>
    <Platform Condition=" '$(Platform)' == '' ">AnyCPU</Platform>
    <OutputType>Library</OutputType>
    <RootNamespace>Plugin</RootNamespace>
    <AssemblyName>Plugin</AssemblyName>
    <TargetFrameworkVersion>{target_framework}</TargetFrameworkVersion>
    <FileAlignment>512</FileAlignment>
  </PropertyGroup>
  <PropertyGroup Condition=" '$(Configuration)|$(Platform)' == 'Debug|AnyCPU' ">
    <OutputPath>bin\\Debug\\</OutputPath>
  </PropertyGroup>
  <ItemGroup>
    <Reference Include="mscorlib" />
    <Reference Include="System" />
    <Reference Include="System.Core" />
    <Reference Include="System.Data" />
    <Reference Include="System.Xml" />
{references}
  </ItemGroup>
  <ItemGroup>
{compiles}
  </ItemGroup>
  <Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets" />
</Project>
"""


def default_msbuild_path() -> str:
    """探测可用 msbuild(优先级从高到低):
    1. `MSBUILD_PATH` 环境变量(显式指定 —— 后端直接读 env,独立于 server.py 参数也可用);
    2. PATH 中的 msbuild(VS 环境);
    3. `FRAMEWORK_MSBUILD_PATH` 环境变量(覆盖 Framework 兜底路径,如系统盘非 C:);
    4. 硬编码 Framework 自带路径(最后兜底);
    全不可用 → 返回 "msbuild" 字符串,交由 subprocess 报错(路径不存在时错误信息清晰)。
    """
    import os
    import shutil
    env = os.getenv("MSBUILD_PATH")
    if env:
        return env
    p = shutil.which("msbuild")
    if p:
        return p
    framework = os.getenv("FRAMEWORK_MSBUILD_PATH") or _FRAMEWORK_MSBUILD
    if Path(framework).exists():
        return framework
    return "msbuild"


class MsbuildCompiler(CompilerBackend):
    def __init__(self, msbuild_path: str | None = None, reference_dlls: list[Path] | None = None,
                 artifact_dir: Path | None = None,
                 target_framework: str = "v4.8"):
        if not reference_dlls:
            raise CompileUnavailableError("金蝶 BOS DLL 未提供,真实编译不可用")
        self.msbuild_path = msbuild_path or default_msbuild_path()
        self.reference_dlls = reference_dlls
        self.artifact_dir = Path(artifact_dir) if artifact_dir is not None else _DEFAULT_ARTIFACT_DIR
        self.target_framework = target_framework

    def compile(self, files: list[CompileFile], project_name: str) -> CompileResult:
        if not files:
            raise ValueError("至少需要一个源文件")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for f in files:
                # 纵深防御:名称必须落在 tmp 内(server 层白名单已挡,直调后端也不能逃逸)
                if Path(f.name).name != f.name or not f.name.endswith(".cs"):
                    raise ValueError(f"非法文件名: {f.name!r}")
                (root / f.name).write_text(f.code, encoding="utf-8")
            csproj = root / "Plugin.csproj"
            refs = "".join(
                f'    <Reference Include="{d.stem}"><HintPath>{d}</HintPath></Reference>'
                for d in self.reference_dlls)
            # csproj <Compile Include> 每文件一条(单文件 = 原行为)
            compiles = "".join(
                f'    <Compile Include="{xml.sax.saxutils.escape(f.name)}" />\n'
                for f in files).rstrip("\n")
            csproj.write_text(
                _CSPROJ_TEMPLATE.format(target_framework=self.target_framework,
                                        references=refs, compiles=compiles),
                encoding="utf-8")
            proc = subprocess.run(
                [self.msbuild_path, str(csproj), "/nologo", "/v:minimal"],
                capture_output=True, text=True, timeout=180)
            raw = (proc.stdout or "") + (proc.stderr or "")
            # DLL 字节必须在临时目录删除前读取(TemporaryDirectory 退出即删,Windows 上延迟读会 FileNotFoundError)
            built_dll = next(Path(tmp).rglob("Plugin.dll"), None)
            dll_bytes = built_dll.read_bytes() if built_dll is not None else None
        result = parse_compile_output(raw)
        # 进程非零退出(msbuild 崩溃/引用缺失/工具链异常)即使无错误行也判失败,不能只信输出文本
        result.success = result.success and proc.returncode == 0
        result.duration_ms = 0
        if result.success and dll_bytes is not None:
            target = self.artifact_dir / project_name / "Plugin.dll"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(dll_bytes)
            result.dll_path = str(target)
        return result
