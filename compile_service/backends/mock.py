"""mock 编译后端:按预设规则表命中错误签名(开发/CI 用,不当质量门)。"""
import re
from pathlib import Path
from compile_service.backends.protocol import CompilerBackend
from compile_service.models import CompileError, CompileResult

DEFAULT_MOCK_RULES = [
    {"code": "CS0103", "pattern": r"xxx\s*\(", "file": "Plugin.cs", "line": 1, "message": "The name 'xxx' does not exist in the current context"},
    {"code": "CS0246", "pattern": r"Kingdee\.BOS", "file": "Plugin.cs", "line": 1, "message": "The type or namespace name could not be found"},
    {"code": "CS0234", "pattern": r"AbstractOperationServicePlugIn", "file": "Plugin.cs", "line": 1, "message": "The type or namespace name does not exist"},
]


class MockCompiler(CompilerBackend):
    def __init__(self, rule_file: Path | None = None):
        self.rules = DEFAULT_MOCK_RULES
        if rule_file:
            import json
            self.rules = json.loads(rule_file.read_text())

    def compile(self, code: str, project_name: str) -> CompileResult:
        errors = []
        for rule in self.rules:
            if re.search(rule["pattern"], code):
                errors.append(CompileError(
                    file=rule["file"], line=rule["line"], code=rule["code"],
                    message=rule["message"], is_fatal=True))
        return CompileResult(success=not errors, raw_output="", duration_ms=0, errors=errors)
