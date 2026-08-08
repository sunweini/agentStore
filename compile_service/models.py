# compile_service/models.py
"""编译服务数据模型。"""
from dataclasses import dataclass, field


class CompileUnavailableError(RuntimeError):
    """编译服务不可用(容器挂/超时)。与编译失败区分:前者不算编译轮次。"""


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
