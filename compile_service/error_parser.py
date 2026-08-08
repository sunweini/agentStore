# compile_service/error_parser.py
"""msbuild 错误输出解析器。

解析流程:
  raw_output ──► 逐行扫描 ──► 匹配错误行 ──► (code,file) 去重 ──► CompileResult
  成功行(无 error)──► success=True
"""
import re
from compile_service.models import CompileError, CompileResult

# 形如: File.cs(12,5): error CS0103: message
_ERROR_RE = re.compile(r"^(.*?)\((\d+),(\d+)\):\s*(?:error|错误)\s+([A-Z]{2}\d+):\s*(.+)$")
_MAX_ERRORS = 10  # 级联洪水聚合上限


def parse_compile_output(raw_output: str) -> CompileResult:
    errors: list[CompileError] = []
    seen: set[tuple[str, str]] = set()  # (code, file) 去重
    for line in raw_output.splitlines():
        m = _ERROR_RE.match(line.strip())
        if not m:
            continue
        file, line_no, _col, code, message = m.groups()
        key = (code, file)
        if key in seen:
            continue
        seen.add(key)
        errors.append(CompileError(file=file, line=int(line_no), code=code, message=message))
        if len(errors) >= _MAX_ERRORS:
            break
    return CompileResult(success=not errors, raw_output=raw_output, duration_ms=0, errors=errors)
