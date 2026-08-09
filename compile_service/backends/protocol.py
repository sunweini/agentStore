"""编译后端协议:mock 与真实 msbuild 共用。"""
from typing import Protocol
from compile_service.models import CompileFile, CompileResult


class CompilerBackend(Protocol):
    def compile(self, files: list[CompileFile], project_name: str) -> CompileResult:
        """编译文件列表,返回结果。真实后端抛 CompileUnavailableError 表示服务不可用。"""
        ...
