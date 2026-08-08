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

def test_compile_result_default_errors():
    # 不传 errors 参数时走 default_factory,不应共享可变默认值
    result = CompileResult(success=True, raw_output="ok", duration_ms=5)
    assert result.errors == []
    assert isinstance(result.errors, list)

from compile_service.error_parser import parse_compile_output
from pathlib import Path

FIX = Path("compile_service/tests/fixtures/msbuild_errors")

def test_parse_basic_error():
    raw = (FIX / "basic_cs0103.txt").read_text()
    result = parse_compile_output(raw)
    assert result.success is False
    assert result.errors[0].code == "CS0103"
    assert result.errors[0].line == 12

def test_parse_cascade_dedup():
    raw = (FIX / "cascade_flood.txt").read_text()  # 6 行洪水,仅 2 个唯一 (code, file)
    result = parse_compile_output(raw)
    assert len(result.errors) == 2  # 去重后的精确数量(<= 10 断言在去重失效时也会通过,故收紧)

def test_parse_cascade_cap():
    raw = (FIX / "cascade_cap.txt").read_text()  # 12 个互不相同的错误,触发 _MAX_ERRORS=10 上限
    result = parse_compile_output(raw)
    assert len(result.errors) == 10  # 聚合上限触发,超出部分被截断

def test_parse_success_output():
    result = parse_compile_output("Build succeeded.\n0 Warning(s)\n0 Error(s)")
    assert result.success is True
    assert result.errors == []

def test_parse_localized_mixed():
    raw = (FIX / "localized_mixed.txt").read_text()  # 中文"错误" + 英文 error 混合
    result = parse_compile_output(raw)
    assert result.success is False
    assert len(result.errors) == 2
    assert result.errors[0].code == "CS0103"
    assert result.errors[1].code == "CS0234"

from compile_service.backends.mock import MockCompiler

def test_mock_compiler_hits_rule():
    mc = MockCompiler(rule_file=None)
    code = "public class P { public void M() { xxx(); } }"  # 命中 CS0103 规则
    result = mc.compile(code=code, project_name="Test")
    assert result.success is False
    assert result.errors[0].code == "CS0103"

def test_mock_compiler_clean_code_passes():
    mc = MockCompiler(rule_file=None)
    result = mc.compile(code="// 无规则命中", project_name="Test")
    assert result.success is True

from fastapi.testclient import TestClient
from compile_service.server import create_app, CompileUnavailableError

def test_health_ok():
    client = TestClient(create_app(backend=MockCompiler()))
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

def test_compile_endpoint():
    client = TestClient(create_app(backend=MockCompiler()))
    r = client.post("/compile", json={"code": "xxx()", "project_name": "T"})
    assert r.status_code == 200
    assert r.json()["success"] is False
    assert r.json()["errors"][0]["code"] == "CS0103"

def test_compile_unavailable_returns_503():
    class DownBackend:
        def compile(self, code, project_name):
            raise CompileUnavailableError("compiler down")
    client = TestClient(create_app(backend=DownBackend()))
    r = client.post("/compile", json={"code": "x", "project_name": "T"})
    assert r.status_code == 503
