# tests/test_compile_service.py
from compile_service.models import CompileError, CompileResult, CompileUnavailableError

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
from compile_service.server import create_app

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

import pytest
from compile_service.backends.msbuild import MsbuildCompiler

def test_msbuild_requires_dlls():
    with pytest.raises(CompileUnavailableError):
        MsbuildCompiler(msbuild_path="msbuild", reference_dlls=[])  # 无 DLL = 不可用

# --- agent 侧编译客户端(真实/mock 无感切换) ---
from agents.kingdee_plugin_agent.tools.compile_client import CompileClient

def test_client_health_ok(monkeypatch):
    client = CompileClient(base_url="http://test")
    monkeypatch.setattr(client.session, "get",
        lambda *a, **k: type("R", (), {"status_code": 200})())
    assert client.health() is True

def test_client_compile_parses_json(monkeypatch):
    client = CompileClient(base_url="http://test")
    # 注:type() 类字典里的 lambda 作为实例方法会被隐式传入 self,须以 *a, **k 接收
    resp = type("R", (), {"status_code": 200, "json": lambda *a, **k: {
        "success": False, "raw_output": "", "duration_ms": 5,
        "errors": [{"file": "P.cs", "line": 1, "code": "CS0103", "message": "m", "is_fatal": True}]}})()
    monkeypatch.setattr(client.session, "post", lambda *a, **k: resp)
    result = client.compile(code="x", project_name="T")
    assert result.errors[0].code == "CS0103"

def test_client_503_raises_unavailable(monkeypatch):
    client = CompileClient(base_url="http://test")
    resp = type("R", (), {"status_code": 503, "json": lambda *a, **k: {"detail": "compiler unavailable"}})()
    monkeypatch.setattr(client.session, "post", lambda *a, **k: resp)
    with pytest.raises(CompileUnavailableError):
        client.compile(code="x", project_name="T")

# --- 终审修复测试:returncode 校验 / REFERENCE_DLLS glob / server+client 往返 ---
from compile_service.server import create_app, _backend_from_env

def test_msbuild_nonzero_returncode_is_failure(monkeypatch):
    # msbuild 进程退出码非 0 且输出无匹配错误行 → 必须判为失败(此前 returncode 被丢弃,误报成功)
    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = ""
    monkeypatch.setattr("compile_service.backends.msbuild.subprocess.run", lambda *a, **k: FakeProc())
    mc = MsbuildCompiler(msbuild_path="msbuild", reference_dlls=[Path("a.dll")])
    result = mc.compile(code="public class P {}", project_name="T")
    assert result.success is False

def test_msbuild_zero_returncode_success_passes(monkeypatch):
    class FakeProc:
        returncode = 0
        stdout = "Build succeeded.\n0 Warning(s)\n0 Error(s)"
        stderr = ""
    monkeypatch.setattr("compile_service.backends.msbuild.subprocess.run", lambda *a, **k: FakeProc())
    mc = MsbuildCompiler(msbuild_path="msbuild", reference_dlls=[Path("a.dll")])
    result = mc.compile(code="public class P {}", project_name="T")
    assert result.success is True

def test_msbuild_zero_returncode_with_errors_still_fails(monkeypatch):
    # 退出码 0 但输出含错误行 → 仍判失败(returncode 校验不得覆盖解析器结论)
    class FakeProc:
        returncode = 0
        stdout = "Plugin.cs(12,5): error CS0103: x"
        stderr = ""
    monkeypatch.setattr("compile_service.backends.msbuild.subprocess.run", lambda *a, **k: FakeProc())
    mc = MsbuildCompiler(msbuild_path="msbuild", reference_dlls=[Path("a.dll")])
    result = mc.compile(code="x", project_name="T")
    assert result.success is False
    assert result.errors[0].code == "CS0103"

def test_backend_from_env_globs_refs_dir(monkeypatch, tmp_path):
    # COMPILE_SERVICE_REQUIRES_DLLS=1 → 对 REFS_DIR 做 *.dll glob(此前只读 REFERENCE_DLLS 环境变量,恒为空)
    monkeypatch.setenv("COMPILE_SERVICE_REQUIRES_DLLS", "1")
    monkeypatch.setenv("REFS_DIR", str(tmp_path))
    (tmp_path / "Kingdee.BOS.dll").write_bytes(b"x")
    (tmp_path / "ignore.txt").write_text("x")
    backend = _backend_from_env()
    assert isinstance(backend, MsbuildCompiler)
    assert [p.name for p in backend.reference_dlls] == ["Kingdee.BOS.dll"]

def test_backend_from_env_empty_refs_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPILE_SERVICE_REQUIRES_DLLS", "1")
    monkeypatch.setenv("REFS_DIR", str(tmp_path))
    with pytest.raises(CompileUnavailableError):
        _backend_from_env()

def test_backend_from_env_missing_refs_dir_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPILE_SERVICE_REQUIRES_DLLS", "1")
    monkeypatch.setenv("REFS_DIR", str(tmp_path / "nonexistent"))
    with pytest.raises(CompileUnavailableError):
        _backend_from_env()

def _round_trip_client(backend) -> CompileClient:
    # 真实 HTTP 往返:以 TestClient(starlette portal 同步传输,直连 ASGI app)作 CompileClient 的 session,
    # 走真实 HTTP 序列化/反序列化。注:httpx 0.28 的 ASGITransport 仅支持异步客户端,而 CompileClient 是同步 Client,故用 TestClient。
    client = CompileClient(base_url="http://test")
    client.session = TestClient(create_app(backend=backend))
    return client

def test_round_trip_compile_matching_rule():
    client = _round_trip_client(MockCompiler())
    result = client.compile(code="public class P { public void M() { xxx(); } }", project_name="T")
    assert result.success is False
    assert isinstance(result.errors[0], CompileError)
    assert result.errors[0].code == "CS0103"

def test_round_trip_clean_code_succeeds():
    client = _round_trip_client(MockCompiler())
    result = client.compile(code="// clean", project_name="T")
    assert result.success is True
    assert result.errors == []

def test_round_trip_unavailable_raises():
    class DownBackend:
        def compile(self, code, project_name):
            raise CompileUnavailableError("compiler down")
    client = _round_trip_client(DownBackend())
    with pytest.raises(CompileUnavailableError):
        client.compile(code="x", project_name="T")
