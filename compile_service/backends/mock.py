"""mock 编译后端:按预设规则表命中错误签名(开发/CI 用,不当质量门)。

规则匹配对象 = **全部文件源码拼接**(多文件项目任一文件命中规则即报错,
行为与单文件一致);错误条目的 file 字段来自规则本身(不追实际命中文件)。
"""
import re
from pathlib import Path
from compile_service.backends.protocol import CompilerBackend
from compile_service.models import CompileFile, CompileError, CompileResult

DEFAULT_MOCK_RULES = [
    {"code": "CS0103", "pattern": r"xxx\s*\(", "file": "Plugin.cs", "line": 1, "message": "The name 'xxx' does not exist in the current context"},
    {"code": "CS0246", "pattern": r"Kingdee\.BOS", "file": "Plugin.cs", "line": 1, "message": "The type or namespace name could not be found"},
    {"code": "CS0234", "pattern": r"AbstractOperationServicePlugIn", "file": "Plugin.cs", "line": 1, "message": "The type or namespace name does not exist"},
]


class MockCompiler(CompilerBackend):
    def __init__(self, rule_file: Path | None = None):
        self.rules = list(DEFAULT_MOCK_RULES)  # 拷贝,防共享可变默认值被外部改动污染
        if rule_file:
            import json
            self.rules = json.loads(rule_file.read_text())

    def compile(self, files: list[CompileFile], project_name: str) -> CompileResult:
        code = "\n".join(f.code for f in files)  # 拼接全部文件源码,规则跨文件命中
        errors = []
        for rule in self.rules:
            if re.search(rule["pattern"], code):
                errors.append(CompileError(
                    file=rule["file"], line=rule["line"], code=rule["code"],
                    message=rule["message"], is_fatal=True))
        return CompileResult(success=not errors, raw_output="", duration_ms=0, errors=errors)
