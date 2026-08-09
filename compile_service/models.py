# compile_service/models.py
"""编译服务数据模型。"""
from dataclasses import dataclass, field


class CompileUnavailableError(RuntimeError):
    """编译服务不可用(容器挂/超时)。与编译失败区分:前者不算编译轮次。"""


@dataclass
class CompileFile:
    """单个待编译源文件(多文件项目:共享辅助类/多个类分文件)。"""
    name: str   # 文件名(仅叶子名,白名单校验防路径穿越/注入)
    code: str


def resolved_files(req) -> list[CompileFile]:
    """请求 → 待编译文件列表:files 显式给出则用之;否则退回单文件 Plugin.cs(code 兼容旧请求)。

    duck-typed(req 只需有 files/code 属性),server.py 的 CompileRequest 与测试假请求均可用。
    """
    if req.files:
        return list(req.files)
    return [CompileFile(name="Plugin.cs", code=req.code)]


@dataclass
class CompileError:
    file: str
    line: int
    code: str
    message: str
    is_fatal: bool = True


@dataclass
class CompileResult:
    success: bool
    raw_output: str
    duration_ms: int
    errors: list[CompileError] = field(default_factory=list)
    dll_path: str = ""   # 编译产物 DLL(服务端留存路径;mock 后端无产出为空)
