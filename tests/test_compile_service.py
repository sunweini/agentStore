# tests/test_compile_service.py
from compile_service.models import CompileError, CompileResult

def test_compile_error_fields():
    err = CompileError(file="Plugin.cs", line=12, code="CS0103", message="x not found", is_fatal=True)
    assert err.file == "Plugin.cs"
    assert err.line == 12
    assert err.code == "CS0103"

def test_compile_result_aggregation():
    result = CompileResult(success=False, errors=[], raw_output="", duration_ms=10)
    result.errors.append(CompileError("A.cs", 1, "CS1", "m", False))
    assert result.success is False
    assert len(result.errors) == 1
