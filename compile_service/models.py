# compile_service/models.py
"""编译服务数据模型。"""
from dataclasses import dataclass, field


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
