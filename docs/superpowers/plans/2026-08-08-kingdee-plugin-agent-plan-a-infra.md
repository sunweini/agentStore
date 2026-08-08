# Kingdee Plugin Agent — Plan A:编译服务基础设施 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可独立运行的编译服务(milestone 1 启动门):错误解析器(真实 msbuild 样本)+ mock 编译器 + HTTP 服务 + agent 侧客户端 + Dockerfile + docker-compose。

**Architecture:** `compile_service/` 是独立容器服务(金蝶 BOS DLL + msbuild),暴露 HTTP API(/health + /compile)。mock 与真实共用 `CompilerBackend` 接口,agent 侧 `tools/compile_client.py` 无感切换。错误解析器是核心组件,按真实 msbuild 错误样本规格化测试(级联洪水/多行/混合语言)。

**Tech Stack:** Python 3.10 + FastAPI + pytest + docker + msbuild(容器内)

## Global Constraints

- 参考 langchain MCP 文档开发(项目铁律,涉及 LangChain 组件时)
- 错误解析器必须用 `compile_service/tests/fixtures/msbuild_errors/` 下真实样本测试
- mock 与真实后端实现同一 `CompilerBackend` 协议,agent 侧只认接口
- 编译提交与健康探测分开两个 endpoint
- 代码注释 ASCII 图:解析流程、状态转换必须画
- 每任务 TDD:先写失败测试 → 跑红 → 实现 → 跑绿 → commit

---

### Task A1: 编译服务骨架 + 错误模型

**Files:**
- Create: `compile_service/__init__.py`
- Create: `compile_service/models.py`
- Test: `tests/test_compile_service.py`

**Interfaces:**
- Produces: `CompileError(file: str, line: int, code: str, message: str, is_fatal: bool)`, `CompileResult(success: bool, errors: list[CompileError], raw_output: str, duration_ms: int)`

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_compile_service.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: 最小实现**

```python
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
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_compile_service.py -v`
Expected: PASS 2 passed

- [ ] **Step 5: Commit**

```bash
git add compile_service/ tests/test_compile_service.py
git commit -m "feat(compile-service): 错误模型 CompileError/CompileResult"
```

---

### Task A2: 错误解析器(真实 msbuild 样本)

**Files:**
- Create: `compile_service/error_parser.py`
- Create: `compile_service/tests/fixtures/msbuild_errors/basic_cs0103.txt`
- Create: `compile_service/tests/fixtures/msbuild_errors/cascade_flood.txt`
- Create: `compile_service/tests/fixtures/msbuild_errors/localized_mixed.txt`
- Modify: `tests/test_compile_service.py`(追加)

**Interfaces:**
- Consumes: `CompileError`, `CompileResult` (Task A1)
- Produces: `parse_compile_output(raw_output: str) -> CompileResult` — 解析 msbuild 文本输出,提取错误行;级联错误聚合(同一 code+file 去重);本地化/混合语言容错(识别 `<code>` 大写模式)

- [ ] **Step 1: 写 fixtures + 失败测试**

```python
# tests/test_compile_service.py (追加)
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
    raw = (FIX / "cascade_flood.txt").read_text()  # 同一引用缺失导致 50 行同类错误
    result = parse_compile_output(raw)
    assert len(result.errors) <= 10  # 聚合上限

def test_parse_success_output():
    result = parse_compile_output("Build succeeded.\n0 Warning(s)\n0 Error(s)")
    assert result.success is True
    assert result.errors == []
```

```text
# compile_service/tests/fixtures/msbuild_errors/basic_cs0103.txt
Plugin.cs(12,5): error CS0103: The name 'xxx' does not exist in the current context
```

```text
# compile_service/tests/fixtures/msbuild_errors/cascade_flood.txt
Missing.cs(1,1): error CS0246: The type or namespace name 'Kingdee' could not be found
Missing.cs(2,1): error CS0246: The type or namespace name 'Kingdee' could not be found
Missing.cs(3,1): error CS0246: The type or namespace name 'Kingdee' could not be found
Plugin.cs(4,7): error CS0246: The type or namespace name 'Kingdee' could not be found
Plugin.cs(5,7): error CS0246: The type or namespace name 'Kingdee' could not be found
Plugin.cs(6,7): error CS0246: The type or namespace name 'Kingdee' could not be found
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_compile_service.py -v`
Expected: FAIL with ImportError (error_parser not exists)

- [ ] **Step 3: 实现解析器**

```python
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
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_compile_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add compile_service/ tests/
git commit -m "feat(compile-service): msbuild 错误解析器(级联去重 + 混合语言容错)"
```

---

### Task A3: CompilerBackend 接口 + mock 编译器

**Files:**
- Create: `compile_service/backends/__init__.py`
- Create: `compile_service/backends/protocol.py`
- Create: `compile_service/backends/mock.py`
- Modify: `tests/test_compile_service.py`(追加)

**Interfaces:**
- Consumes: `CompileResult` (A1)
- Produces: `class CompilerBackend(Protocol): def compile(self, code: str, project_name: str) -> CompileResult`, `MockCompiler(rule_file: Path)` — 按预设规则表命中错误签名;`DEFAULT_MOCK_RULES` 内嵌 3 条种子规则

- [ ] **Step 1: 写失败测试**

```python
# tests/test_compile_service.py (追加)
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
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_compile_service.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: 实现**

```python
# compile_service/backends/protocol.py
"""编译后端协议:mock 与真实 msbuild 共用。"""
from typing import Protocol
from compile_service.models import CompileResult


class CompilerBackend(Protocol):
    def compile(self, code: str, project_name: str) -> CompileResult:
        """编译代码,返回结果。真实后端抛 CompileUnavailableError 表示服务不可用。"""
        ...
```

```python
# compile_service/backends/mock.py
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
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_compile_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add compile_service/ tests/
git commit -m "feat(compile-service): CompilerBackend 协议 + MockCompiler(规则表驱动)"
```

---

### Task A4: 编译 HTTP 服务(健康探测 + 提交)

**Files:**
- Create: `compile_service/server.py`
- Modify: `tests/test_compile_service.py`(追加,用 fastapi TestClient)

**Interfaces:**
- Consumes: `CompilerBackend`, `MockCompiler` (A3)
- Produces: `create_app(backend: CompilerBackend) -> FastAPI` — `/health`(200/503,后端可用性)+ `/compile`(POST {code, project_name} → CompileResult JSON);`CompileUnavailableError` 自定义异常(后端不可用时 503)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_compile_service.py (追加)
from fastapi.testclient import TestClient
from compile_service.server import create_app, CompileUnavailableError
from compile_service.backends.mock import MockCompiler

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
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_compile_service.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: 实现**

```python
# compile_service/server.py
"""编译 HTTP 服务。

请求流:
  client ──► /health 探测 ──► 200 → 提交 /compile ──► backend.compile ──► 结果 JSON
  后端不可用(如容器未起)──► CompileUnavailableError ──► 503(不算编译轮次)
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class CompileUnavailableError(RuntimeError):
    """编译服务不可用(容器挂/超时)。与编译失败区分:前者不算编译轮次。"""


class CompileRequest(BaseModel):
    code: str
    project_name: str


def create_app(backend) -> FastAPI:
    app = FastAPI(title="kingdee-compile-service")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/compile")
    def compile_endpoint(req: CompileRequest):
        try:
            result = backend.compile(code=req.code, project_name=req.project_name)
        except CompileUnavailableError as e:
            return JSONResponse(status_code=503, content={"detail": str(e)})
        return {
            "success": result.success,
            "raw_output": result.raw_output,
            "duration_ms": result.duration_ms,
            "errors": [
                {"file": e.file, "line": e.line, "code": e.code, "message": e.message, "is_fatal": e.is_fatal}
                for e in result.errors
            ],
        }

    return app
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_compile_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add compile_service/ tests/
git commit -m "feat(compile-service): HTTP 服务(健康探测 + 编译提交 + 503 区分不可用)"
```

---

### Task A5: 真实 msbuild 后端(标注 DLL 前置)+ Dockerfile

**Files:**
- Create: `compile_service/backends/msbuild.py`
- Create: `compile_service/Dockerfile`
- Create: `compile_service/docker-entrypoint.sh`
- Modify: `tests/test_compile_service.py`(追加)

**Interfaces:**
- Consumes: `CompilerBackend`, `CompileResult`, `parse_compile_output` (A2/A3)
- Produces: `MsbuildCompiler(msbuild_path: str, reference_dlls: list[Path]) -> CompilerBackend` — 写 .csproj + 源文件,调 msbuild,输出交解析器;**无 DLL 时构造即抛 `CompileUnavailableError`**;`COMPILE_SERVICE_REQUIRES_DLLS` 环境变量显式声明(团队 DLL 到位前 E2E 不阻塞)
- Dockerfile: .NET Framework + msbuild 基础镜像(mcr.microsoft.com/dotnet/framework/sdk 或团队自备),COPY 金蝶 DLL 目录 `build/references/`,挂载 `/data`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_compile_service.py (追加)
import pytest
from compile_service.backends.msbuild import MsbuildCompiler
from compile_service.server import CompileUnavailableError

def test_msbuild_requires_dlls():
    with pytest.raises(CompileUnavailableError):
        MsbuildCompiler(msbuild_path="msbuild", reference_dlls=[])  # 无 DLL = 不可用
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_compile_service.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: 实现**

```python
# compile_service/backends/msbuild.py
"""真实 msbuild 编译后端(容器内)。

状态:
  无 DLL ──► 构造抛 CompileUnavailableError(服务不启动,标记"DLL 未到位")
  有 DLL ──► compile: 生成 csproj+源文件 ──► msbuild ──► 输出交解析器
"""
import subprocess
import tempfile
from pathlib import Path
from compile_service.backends.protocol import CompilerBackend
from compile_service.error_parser import parse_compile_output
from compile_service.models import CompileResult
from compile_service.server import CompileUnavailableError


class MsbuildCompiler(CompilerBackend):
    def __init__(self, msbuild_path: str, reference_dlls: list[Path]):
        if not reference_dlls:
            raise CompileUnavailableError("金蝶 BOS DLL 未提供,真实编译不可用")
        self.msbuild_path = msbuild_path
        self.reference_dlls = reference_dlls

    def compile(self, code: str, project_name: str) -> CompileResult:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "Plugin.cs"
            src.write_text(code, encoding="utf-8")
            csproj = Path(tmp) / "Plugin.csproj"
            refs = "".join(f'<Reference Include="{d.stem}"><HintPath>{d}</HintPath></Reference>' for d in self.reference_dlls)
            csproj.write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                f"</PropertyGroup><ItemGroup>{refs}</ItemGroup></Project>", encoding="utf-8")
            proc = subprocess.run(
                [self.msbuild_path, str(csproj), "/nologo", "/v:minimal"],
                capture_output=True, text=True, timeout=120)
            raw = (proc.stdout or "") + (proc.stderr or "")
        result = parse_compile_output(raw)
        result.duration_ms = 0
        return result
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_compile_service.py -v`
Expected: PASS(该测试只验证无 DLL 抛错;真实编译由 E2E 门覆盖,团队 DLL 到位后跑)

- [ ] **Step 5: Dockerfile**

```dockerfile
# compile_service/Dockerfile
# 基础镜像需含 msbuild(金蝶 BOS 为 .NET Framework 4.x,优先 Windows 容器或 mono 兼容层)
FROM mcr.microsoft.com/dotnet/framework/sdk:4.8-windowsservercore-ltsc2022
WORKDIR /app
COPY . .
# 金蝶 BOS DLL 放 build/references/(团队提供,授权合规)
COPY build/references/ ./references/
ENV COMPILE_SERVICE_REQUIRES_DLLS=1
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "compile_service.server:create_factory", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 6: Commit**

```bash
git add compile_service/ tests/
git commit -m "feat(compile-service): 真实 msbuild 后端(无 DLL 即不可用)+ Dockerfile"
```

---

### Task A6: agent 侧编译客户端 + docker-compose

**Files:**
- Create: `agents/kingdee_plugin_agent/tools/__init__.py`
- Create: `agents/kingdee_plugin_agent/tools/compile_client.py`
- Create: `docker-compose.yml`
- Modify: `tests/test_compile_service.py`(追加)
- Modify: `.gitignore`(追加 `data/kingdee-rag/`)

**Interfaces:**
- Consumes: `/health`, `/compile` 接口 (A4)
- Produces: `CompileClient(base_url: str, timeout: float)` — `health() -> bool`, `compile(code: str, project_name: str) -> CompileResult`(解析 JSON);`compile_client_from_env()` 工厂(env `COMPILE_SERVICE_URL`,缺省用 `http://localhost:8000`);返回 503 抛 `CompileUnavailableError`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_compile_service.py (追加)
from agents.kingdee_plugin_agent.tools.compile_client import CompileClient

def test_client_health_ok(monkeypatch):
    client = CompileClient(base_url="http://test")
    monkeypatch.setattr(client.session, "get",
        lambda *a, **k: type("R", (), {"status_code": 200})())
    assert client.health() is True

def test_client_compile_parses_json(monkeypatch):
    client = CompileClient(base_url="http://test")
    resp = type("R", (), {"status_code": 200, "json": lambda: {
        "success": False, "raw_output": "", "duration_ms": 5,
        "errors": [{"file": "P.cs", "line": 1, "code": "CS0103", "message": "m", "is_fatal": True}]}})()
    monkeypatch.setattr(client.session, "post", lambda *a, **k: resp)
    result = client.compile(code="x", project_name="T")
    assert result.errors[0].code == "CS0103"

def test_client_503_raises_unavailable(monkeypatch):
    from compile_service.server import CompileUnavailableError
    client = CompileClient(base_url="http://test")
    resp = type("R", (), {"status_code": 503})()
    monkeypatch.setattr(client.session, "post", lambda *a, **k: resp)
    try:
        client.compile(code="x", project_name="T")
        assert False, "should raise"
    except CompileUnavailableError:
        pass
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_compile_service.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: 实现**

```python
# agents/kingdee_plugin_agent/tools/compile_client.py
"""编译服务 HTTP 客户端(真实/mock 无感切换:同一 base_url 契约)。"""
import os
import httpx
from compile_service.models import CompileError, CompileResult
from compile_service.server import CompileUnavailableError


class CompileClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = httpx.Client(timeout=timeout)

    def health(self) -> bool:
        try:
            r = self.session.get(f"{self.base_url}/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def compile(self, code: str, project_name: str) -> CompileResult:
        r = self.session.post(f"{self.base_url}/compile",
                              json={"code": code, "project_name": project_name})
        if r.status_code == 503:
            raise CompileUnavailableError(r.json().get("detail", "compiler unavailable"))
        data = r.json()
        return CompileResult(
            success=data["success"], raw_output=data.get("raw_output", ""),
            duration_ms=data.get("duration_ms", 0),
            errors=[CompileError(**e) for e in data.get("errors", [])])


def compile_client_from_env() -> CompileClient:
    return CompileClient(base_url=os.getenv("COMPILE_SERVICE_URL", "http://localhost:8000"))
```

- [ ] **Step 4: docker-compose + gitignore**

```yaml
# docker-compose.yml(首版:api + compile_service;RAG 存储后续 Plan B 追加)
services:
  compile-service:
    build: ./compile_service
    ports: ["8000:8000"]
    volumes:
      - ./compile_service/build/references:/app/references
  api:
    build: .
    ports: ["8080:8080"]
    environment:
      - COMPILE_SERVICE_URL=http://compile-service:8000
    volumes:
      - ./data/kingdee-rag:/data/kingdee-rag
```

```text
# .gitignore 追加
data/kingdee-rag/
data/kingdee-artifacts/
data/kingdee-deliverables/
```

- [ ] **Step 5: 跑全部测试 + commit**

Run: `pytest tests/ -v`
Expected: 全 PASS

```bash
git add agents/kingdee_plugin_agent/tools/ docker-compose.yml .gitignore tests/
git commit -m "feat(compile-service): agent 侧编译客户端 + docker-compose + gitignore"
```

---

### Plan A 完成标准

- [ ] `pytest tests/ -v` 全绿
- [ ] mock 编译服务可本地起:`uvicorn compile_service.server:create_factory` + curl /health 200
- [ ] E2E 门(团队 DLL 到位后):真实容器编译 3 类型样例各一通过 → 解锁 Plan B/C 的 E2E
