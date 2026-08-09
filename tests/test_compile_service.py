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


def test_compile_endpoint_reports_dll_path():
    """/compile 响应带 dll_path(后端产出);mock 后端无产出 → 空串。"""
    class WithDll:
        def compile(self, code, project_name):
            return CompileResult(success=True, raw_output="", duration_ms=0,
                                 errors=[], dll_path="/artifacts/T/Plugin.dll")
    client = TestClient(create_app(backend=WithDll()))
    r = client.post("/compile", json={"code": "x", "project_name": "T"})
    assert r.json()["dll_path"] == "/artifacts/T/Plugin.dll"
    r2 = TestClient(create_app(backend=MockCompiler())).post(
        "/compile", json={"code": "class X {}", "project_name": "T"})
    assert r2.json()["dll_path"] == ""


def test_dll_download_endpoint():
    """GET /dll/<project_name> 拉取留存 DLL;非法名 400;后端无留存 404。"""
    from compile_service.backends.msbuild import MsbuildCompiler
    artifact_dir = Path("data/kingdee-compiled-test")
    dll = artifact_dir / "T" / "Plugin.dll"
    dll.parent.mkdir(parents=True, exist_ok=True)
    dll.write_bytes(b"PE\x00\x00")
    try:
        backend = MsbuildCompiler(msbuild_path="msbuild",
                                  reference_dlls=[Path("a.dll")],
                                  artifact_dir=artifact_dir)
        client = TestClient(create_app(backend=backend))
        r = client.get("/dll/T")
        assert r.status_code == 200 and r.content == b"PE\x00\x00"
        assert client.get("/dll/nope").status_code == 404
        # ../ 在路由层已被规范化拦截(404,不达 handler)—— handler 层白名单另行单测
        assert client.get("/dll/../evil").status_code == 404
        # mock 后端无 artifact_dir → 404
        r2 = TestClient(create_app(backend=MockCompiler())).get("/dll/T")
        assert r2.status_code == 404
    finally:
        import shutil
        shutil.rmtree(artifact_dir, ignore_errors=True)


def test_dll_project_name_whitelist():
    """DLL 下载 project_name 白名单(与 ArtifactStore 同源,防路径穿越)。"""
    from compile_service.server import _PROJECT_NAME_RE
    assert _PROJECT_NAME_RE.match("A1") and _PROJECT_NAME_RE.match("a_b-1")
    assert not _PROJECT_NAME_RE.match("../evil")
    assert not _PROJECT_NAME_RE.match("a/b")
    assert not _PROJECT_NAME_RE.match("a b")

def test_compile_unavailable_returns_503():
    class DownBackend:
        def compile(self, code, project_name):
            raise CompileUnavailableError("compiler down")
    client = TestClient(create_app(backend=DownBackend()))
    r = client.post("/compile", json={"code": "x", "project_name": "T"})
    assert r.status_code == 503


def test_compile_rejects_bad_project_name_no_file_written(tmp_path, monkeypatch):
    """写侧路径穿越防护(评审 Important):非法 project_name(如 ../../references)
    → 400,后端不被调用、artifact_dir 不产生任何文件(校验在 backend.compile 之前)。"""
    from compile_service.backends.msbuild import MsbuildCompiler
    artifact_dir = tmp_path / "out"
    backend = MsbuildCompiler(msbuild_path="msbuild", reference_dlls=[Path("a.dll")],
                              artifact_dir=artifact_dir)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("P", (), {
        "stdout": "Build succeeded.", "stderr": "", "returncode": 0})())
    client = TestClient(create_app(backend=backend))
    r = client.post("/compile", json={"code": "x", "project_name": "../../references"})
    assert r.status_code == 400
    assert not artifact_dir.exists()      # 未触达后端 → 无任何落盘
    r2 = client.post("/compile", json={"code": "x", "project_name": "A1"})
    assert r2.status_code == 200 and r2.json()["success"] is True

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


def test_client_fetches_dll_to_local_artifact_dir(monkeypatch, tmp_path):
    """编译成功且服务端产出 DLL → 客户端拉到本地 artifact_dir,dll_path 为本地路径。"""
    client = CompileClient(base_url="http://test", artifact_dir=tmp_path / "compiled")
    resp = type("R", (), {"status_code": 200, "json": lambda *a, **k: {
        "success": True, "raw_output": "", "duration_ms": 5,
        "dll_path": "/artifacts/T/Plugin.dll", "errors": []}})()
    dll_resp = type("R", (), {"status_code": 200, "content": b"PE\x00\x00"})()
    monkeypatch.setattr(client.session, "post", lambda *a, **k: resp)
    monkeypatch.setattr(client.session, "get", lambda *a, **k: dll_resp)
    result = client.compile(code="x", project_name="T")
    local = tmp_path / "compiled" / "T" / "Plugin.dll"
    assert result.dll_path == str(local)
    assert local.read_bytes() == b"PE\x00\x00"


def test_client_dll_fetch_failure_degrades_to_empty(monkeypatch, tmp_path):
    """服务端产出 DLL 但拉取失败(404/网络)→ dll_path 置空(冒烟按无 DLL 跳过,不崩)。"""
    client = CompileClient(base_url="http://test", artifact_dir=tmp_path / "compiled")
    resp = type("R", (), {"status_code": 200, "json": lambda *a, **k: {
        "success": True, "raw_output": "", "duration_ms": 5,
        "dll_path": "/artifacts/T/Plugin.dll", "errors": []}})()
    monkeypatch.setattr(client.session, "post", lambda *a, **k: resp)
    monkeypatch.setattr(client.session, "get", lambda *a, **k:
                        type("R", (), {"status_code": 404})())
    result = client.compile(code="x", project_name="T")
    assert result.dll_path == ""


def test_client_dll_fetch_oserror_degrades_to_empty(monkeypatch, tmp_path):
    """落盘失败(评审 Minor:磁盘满/权限,mkdir 抛 OSError)→ dll_path 置空
    (降级契约,不把 w5 节点打崩)。FileExistsError 系 OSError 子类。"""
    client = CompileClient(base_url="http://test", artifact_dir=tmp_path / "compiled")
    resp = type("R", (), {"status_code": 200, "json": lambda *a, **k: {
        "success": True, "raw_output": "", "duration_ms": 5,
        "dll_path": "/artifacts/T/Plugin.dll", "errors": []}})()
    dll_resp = type("R", (), {"status_code": 200, "content": b"PE\x00\x00"})()
    monkeypatch.setattr(client.session, "post", lambda *a, **k: resp)
    monkeypatch.setattr(client.session, "get", lambda *a, **k: dll_resp)
    # artifact_dir 是已存在的文件 → target.parent.mkdir 抛 FileExistsError(OSError)
    (tmp_path / "compiled").write_text("i am a file", encoding="utf-8")
    result = client.compile(code="x", project_name="T")
    assert result.dll_path == ""

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

# --- 部署路径全配置化:env 覆盖 + 代码相对默认(零硬编码路径) ---
from compile_service.backends.msbuild import default_msbuild_path, MsbuildCompiler, _DEFAULT_ARTIFACT_DIR

def test_default_msbuild_path_msbuild_env_respected(monkeypatch, tmp_path):
    """MSBUILD_PATH 环境变量最高优先(后端直接读 env,独立于 server.py 参数也可用),压过 PATH 探测。"""
    p = tmp_path / "custom" / "MSBuild.exe"
    p.parent.mkdir()
    p.write_bytes(b"")
    monkeypatch.setenv("MSBUILD_PATH", str(p))
    monkeypatch.setenv("FRAMEWORK_MSBUILD_PATH", str(tmp_path / "nonexistent.exe"))
    monkeypatch.setattr("shutil.which", lambda *a, **k: "/path/msbuild.exe")  # PATH 命中也不应覆盖 env
    assert default_msbuild_path() == str(p)

def test_default_msbuild_path_framework_env_override(monkeypatch, tmp_path):
    """FRAMEWORK_MSBUILD_PATH 覆盖 Framework 兜底路径(缺省为硬编码 Windows 路径)。"""
    p = tmp_path / "framework" / "MSBuild.exe"
    p.parent.mkdir()
    p.write_bytes(b"")
    monkeypatch.delenv("MSBUILD_PATH", raising=False)
    monkeypatch.setenv("FRAMEWORK_MSBUILD_PATH", str(p))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert default_msbuild_path() == str(p)

def test_default_msbuild_path_framework_fallback_intact(monkeypatch):
    """无 env 覆盖且 PATH 无 msbuild → 仍回退硬编码 Framework 路径(最后兜底,默认值未破坏)。"""
    from compile_service.backends import msbuild as msb
    monkeypatch.delenv("MSBUILD_PATH", raising=False)
    monkeypatch.delenv("FRAMEWORK_MSBUILD_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    # Linux 上无法真实创建 C:\... 路径,模拟其 exists(仅对该路径生效)
    monkeypatch.setattr(msb.Path, "exists", lambda self: str(self) == msb._FRAMEWORK_MSBUILD)
    assert default_msbuild_path() == msb._FRAMEWORK_MSBUILD

def test_artifact_dir_default_code_relative():
    """artifact_dir 缺省 = 代码相对 仓库根/data/kingdee-compiled(compile_service/.. 解析后),非 cwd 相对。"""
    from compile_service.backends import msbuild as msb
    mc = MsbuildCompiler(msbuild_path="msbuild", reference_dlls=[Path("a.dll")])
    expected = Path(msb.__file__).resolve().parent.parent.parent / "data" / "kingdee-compiled"
    assert mc.artifact_dir == expected
    assert str(mc.artifact_dir).endswith("data/kingdee-compiled")

def test_backend_from_env_default_refs_dir_code_relative(monkeypatch):
    """REFS_DIR 缺省 = 代码相对 compile_service/build/references(非容器路径 /app/references)。"""
    from compile_service.server import _backend_from_env, _DEFAULT_REFS_DIR
    assert str(_DEFAULT_REFS_DIR).endswith("compile_service/build/references")
    dll = _DEFAULT_REFS_DIR / "tmp-kingdee-refs-test.dll"
    dll.write_bytes(b"x")
    try:
        monkeypatch.setenv("COMPILE_SERVICE_REQUIRES_DLLS", "1")
        monkeypatch.delenv("REFS_DIR", raising=False)
        backend = _backend_from_env()
        assert isinstance(backend, MsbuildCompiler)
        assert "tmp-kingdee-refs-test.dll" in [p.name for p in backend.reference_dlls]
        # 未配 COMPILE_ARTIFACT_DIR → artifact_dir 走代码相对默认
        assert backend.artifact_dir == _DEFAULT_ARTIFACT_DIR
    finally:
        dll.unlink(missing_ok=True)

def test_backend_from_env_artifact_dir_env(monkeypatch, tmp_path):
    """COMPILE_ARTIFACT_DIR 环境变量 → 透传给 MsbuildCompiler.artifact_dir(留存目录可配)。"""
    monkeypatch.setenv("COMPILE_SERVICE_REQUIRES_DLLS", "1")
    monkeypatch.setenv("REFS_DIR", str(tmp_path))
    (tmp_path / "Kingdee.BOS.dll").write_bytes(b"x")
    monkeypatch.setenv("COMPILE_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    backend = _backend_from_env()
    assert isinstance(backend, MsbuildCompiler)
    assert backend.artifact_dir == (tmp_path / "artifacts")
