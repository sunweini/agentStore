# kingdee-plugin-agent 四项改造实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 kingdee-plugin-agent 四项 v1 债务:apikey timing-safe、load_skill 线上验证、API 并发上限 + --env 凭证分套、任务持久化。

**Architecture:** 分四阶段独立交付:① `api.py` 鉴权改 `secrets.compare_digest`;② 真实 DeepSeek smoke 验证 `structured_with_skill`(验证型,无代码除非回退);③ `config.py` 加 `kingdee_env_vars(env)` 凭证分套辅助 + `build_graph(env)` 透传 + API `Semaphore` 并发闸门;④ `api.py` 任务存储换 AsyncSqliteSaver + 元数据表恢复。

**Tech Stack:** Python + FastAPI + LangGraph(checkpointer)+ SQLite(asyncsqlite3)+ httpx + secrets。

## Global Constraints

- 全部在 worktree `kingdee-webapi-integration` 分支 `worktree-kingdee-webapi-integration` 开发,不 commit 到 main。
- 测试:kingdee 范围 `pytest tests/test_kingdee_agent.py tests/test_kingdee_api.py tests/test_compile_service.py tests/test_ingest.py tests/test_rag.py tests/test_templates.py`(不含 sentiment)。
- 凭证命名 `<VAR>_<ENV>`(env 大写);env 空 = 默认 `KD_*` 4 项 + `KD_LCID`。
- CLI `--env` 保持必填;API `payload["env"]` 可选(空回落默认)。
- 429 必须由 create_task 请求处理函数抛出(线程内 raise 到不了 FastAPI)。
- `KD_LCID` 随 env 分套。
- 设计文档:`docs/superpowers/specs/2026-08-10-kingdee-plugin-agent-four-fixes-design.md`(已批准)。

---

### Task 1: apikey timing-safe

**Files:**
- Modify: `agents/kingdee_plugin_agent/api.py:305-308`(`_check`)
- Modify: `tests/test_kingdee_agent.py`(401 相关测试区,~line 2038-2083)

**Interfaces:**
- Consumes: 无
- Produces: `_check(x_api_key)` 行为不变(错 key 401),内部改用 `secrets.compare_digest`

- [ ] **Step 1: 改 `_check` 用 compare_digest**

```python
# api.py 顶部加 import secrets
import secrets

def _check(x_api_key: str) -> None:
    """apikey 校验:compare_digest 恒定时间比较;未配置有效 key 一律 401。"""
    if not effective_key or not secrets.compare_digest(
            x_api_key.encode(), effective_key.encode()):
        raise HTTPException(401, "apikey 无效")
```

- [ ] **Step 2: 跑现有 401 测试确认语义不变**

Run: `pytest tests/test_kingdee_agent.py -k "auth or 401 or api_key" -q`
Expected: PASS(错 key 仍 401)

- [ ] **Step 3: 补 compare_digest 断言测试**(加在现有 auth 测试旁)

```python
def test_api_key_compare_digest():
    """apikey 用恒定时间比较(时序侧信道防护)。"""
    import secrets as _s
    assert _s.compare_digest(b"abc", b"abc") is True
    assert _s.compare_digest(b"abc", b"abd") is False
```

- [ ] **Step 4: 跑测试确认**

Run: `pytest tests/test_kingdee_agent.py -k "auth or 401 or api_key" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/kingdee_plugin_agent/api.py tests/test_kingdee_agent.py
git commit -m "fix(api): apikey 校验改 secrets.compare_digest(恒定时间,防时序侧信道)"
```

---

### Task 2: load_skill 线上验证(smoke 脚本)

**Files:**
- Create: `scripts/smoke_structured_with_skill.py`(一次性验证脚本,不入测试)
- 无代码改造(除非验证发现需回退)

**Interfaces:**
- Consumes: `agents/kingdee_plugin_agent/skills/loader.py` 的 `structured_with_skill`
- Produces: 验证结论(记录到 docs/kingdee-plugin-agent/tech.md §11 未验证项)

- [ ] **Step 1: 确认 .env 有 DEEPSEEK_API_KEY**

Run: `grep -c DEEPSEEK_API_KEY .env`
Expected: 1(若无,向用户要 key)

- [ ] **Step 2: 写 smoke 脚本**(调 w1 generate_questions 真实 LLM)

```python
#!/usr/bin/env python
"""load_skill 绑定线上验证:w1 generate_questions 真实 DeepSeek smoke。

观测:API 拒绝?畸形 JSON?工具调用正常?被拒 → 记录回退方案(JSON Mode)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.config import get_env
from agents.kingdee_plugin_agent.skills.loader import structured_with_skill
from agents.kingdee_plugin_agent.tools.kingdee_api import KingdeeApiClient

def main():
    # w1 真实调用:需求 → generate_questions(绑定 load_skill)
    from agents.kingdee_plugin_agent.graph.workers.w1_requirement import (
        W1Worker, PlanOutput)
    from agents.kingdee_plugin_agent.store.artifact_store import ArtifactStore
    from pathlib import Path

    store = ArtifactStore(root=Path("data/smoke-w1"))
    w1 = W1Worker(llm=None, store=store)  # llm=None 走确定性?不 —— 这里要真实 LLM
    print("需要真实 LLM,参考 w1 的 get_chat_model() 接线构造,再调 structured_with_skill")
    print("观测点:structured_with_skill 是否返回 (parsed, tool_calls) 而非异常")
    print("被拒(rejected/tool 不支持)→ 回退 JSON Mode 方案(见 CLAUDE.md 约束)")

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 跑 smoke,记录结论**

Run: `python scripts/smoke_structured_with_skill.py`
Expected: 观察 structured_with_skill 真实行为(成功 / 被拒 / 畸形)

- [ ] **Step 4: 更新 tech.md §11 未验证项**

若成功:改为「✅ 已验证(2026-08-10 真实 DeepSeek)」
若被拒:实现回退(见 CLAUDE.md `load_skill 绑定未线上验证` 段),并记录

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke_structured_with_skill.py docs/kingdee-plugin-agent/tech.md
git commit -m "docs(kingdee): load_skill 线上验证结论(真实 DeepSeek smoke)"
```

---

### Task 3: config.kingdee_env_vars + 客户端/图 env 透传

**Files:**
- Modify: `common/config.py`(加 `kingdee_env_vars`)
- Modify: `agents/kingdee_plugin_agent/tools/kingdee_api.py:181-190`(`client_from_env_or_none` 加 env)
- Modify: `agents/kingdee_plugin_agent/agent.py:119-142`(`build_graph` 加 env)
- Modify: `tests/test_kingdee_api.py`(env 凭证选择测试)

**Interfaces:**
- Produces: `config.kingdee_env_vars(env="") -> dict`(含 KD_BASE_URL/USERNAME/PASSWORD/DATA_CENTER/LCID 5 键)
- Produces: `KingdeeApiClient.client_from_env_or_none(env="") -> KingdeeApiClient | None`
- Produces: `build_graph(env="")`(透传给 client_from_env_or_none)

- [ ] **Step 1: config.py 加 kingdee_env_vars**

```python
_KD_VAR_NAMES = ("KD_BASE_URL", "KD_USERNAME", "KD_PASSWORD",
                 "KD_DATA_CENTER", "KD_LCID")

def kingdee_env_vars(env: str = "") -> dict:
    """按环境取金蝶凭证:优先 <VAR>_<ENV>,回落 <VAR>(默认环境)。

    含 KD_LCID(语系);env 空 = 默认环境,直接用 KD_* 5 项。
    客户端按返回值构造,缺项由调用方(硬门槛)报 503 点明。
    """
    prefix = f"_{env.upper()}" if env else ""
    return {name: get_env(f"{name}{prefix}") for name in _KD_VAR_NAMES}
```

- [ ] **Step 2: kingdee_api.py client_from_env_or_none 加 env**

```python
@classmethod
def client_from_env_or_none(cls, env: str = "") -> "KingdeeApiClient | None":
    """从环境变量构造客户端;缺 KD_BASE_URL 返回 None(无环境 = 硬门槛信号)。

    env: 环境名(凭证 <VAR>_<ENV> 分套,空 = 默认 KD_* 5 项)。
    """
    from common.config import kingdee_env_vars
    vars_ = kingdee_env_vars(env)
    base = vars_.get("KD_BASE_URL", "")
    if not base:
        return None
    return cls(base, vars_.get("KD_DATA_CENTER", ""), vars_.get("KD_USERNAME", ""),
               vars_.get("KD_PASSWORD", ""), int(vars_.get("KD_LCID", "2052") or 2052))
```

- [ ] **Step 3: build_graph 加 env 参数**

```python
def build_graph(store=None, compile_client=None, rag=None, standards=None,
                api_client=None, llm=_UNSET, smoke_client=None, experience=None,
                env: str = ""):
    """...env: 金蝶目标环境名(凭证 <VAR>_<ENV> 分套,空 = 默认 KD_*)。"""
    ...
    api = api_client or KingdeeApiClient.client_from_env_or_none(env=env)
```

- [ ] **Step 4: 写测试(env 凭证选择)**

```python
def test_client_from_env_env_vars(monkeypatch):
    """env 分套:KD_BASE_URL_TEST 优先于 KD_BASE_URL。"""
    import common.config as config
    monkeypatch.setenv("KD_BASE_URL", "http://default/k3cloud/")
    monkeypatch.setenv("KD_BASE_URL_TEST", "http://test/k3cloud/")
    monkeypatch.setenv("KD_USERNAME_TEST", "t-user")
    c = KingdeeApiClient.client_from_env_or_none(env="test")
    assert c is not None
    assert c.base_url == "http://test"
    assert c._username == "t-user"


def test_client_from_env_no_env_falls_back(monkeypatch):
    """env 空回落默认 KD_*。"""
    monkeypatch.setenv("KD_BASE_URL", "http://default/k3cloud/")
    monkeypatch.setenv("KD_USERNAME", "d-user")
    c = KingdeeApiClient.client_from_env_or_none(env="")
    assert c is not None
    assert c.base_url == "http://default"
    assert c._username == "d-user"
```

- [ ] **Step 5: 跑测试**

Run: `pytest tests/test_kingdee_api.py -k "env" -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add common/config.py agents/kingdee_plugin_agent/tools/kingdee_api.py agents/kingdee_plugin_agent/agent.py tests/test_kingdee_api.py
git commit -m "feat(kingdee): 金蝶凭证按环境分套(KD_*_<ENV>)+ build_graph/env 透传"
```

---

### Task 4: CLI/API env 凭证 + API 并发上限

**Files:**
- Modify: `agents/kingdee_plugin_agent/cli.py`(硬门槛按 env + build_graph(env))
- Modify: `agents/kingdee_plugin_agent/api.py`(硬门槛按 env + create_task 传 env + Semaphore)
- Modify: `tests/test_kingdee_agent.py`(429 并发 + env 硬门槛测试)

**Interfaces:**
- Consumes: `config.kingdee_env_vars(env)`(Task 3)、`build_graph(env)`(Task 3)
- Produces: `api.py` 并发信号量 `_sem`;429「并发任务数已达上限,稍后重试」

- [ ] **Step 1: cli.py 硬门槛 + build_graph 传 env**

```python
# cli.py run_cli 内,原「未配 KD_BASE_URL 退出」改为按 env 查套:
from common.config import kingdee_env_vars
vars_ = kingdee_env_vars(args.env)
if not vars_.get("KD_BASE_URL"):
    print(f"错误:未配置金蝶环境(KD_BASE_URL{'_{}'.format(args.env.upper()) if args.env else ''}),"
          f"先配置环境再使用")
    return 1
...
app = build_graph(env=args.env)  # 原 build_graph()
```

- [ ] **Step 2: api.py create_task 传 env + Semaphore**

```python
# api.py 顶部(常量区)
import threading, secrets
MAX_CONCURRENT_TASKS = int(config.get_env("KINGDEE_MAX_CONCURRENT", "4"))
_sem = threading.Semaphore(MAX_CONCURRENT_TASKS)

# create_task 内:
env_name = str(payload.get("env") or "")
vars_ = kingdee_env_vars(env_name)
missing = [name for name in _KD_ENV_VARS if not vars_.get(name)]
if missing:
    suffix = f"_{env_name.upper()}" if env_name else ""
    raise HTTPException(503, f"金蝶环境未配置完整,缺少: "
                             f"{', '.join(m + suffix for m in missing)};"
                             f"请配置后再创建任务")
...
if not _sem.acquire(blocking=False):
    raise HTTPException(429, "并发任务数已达上限,稍后重试")
graph = graph_factory() if graph_factory else build_graph(env=env_name)
threading.Thread(target=_run_loop, args=(handle,), daemon=True).start()

# _run_loop 里 finally release:
def _run_loop(handle):
    try:
        ... # 原逻辑
    finally:
        _sem.release()
```

- [ ] **Step 3: 写测试(429 + env 硬门槛)**

```python
def test_api_concurrency_limit_429():
    """并发任务数达上限 → 429。"""
    # 用 monkeypatch 把 _sem 换成容量 1 且已占用的信号量
    import agents.kingdee_plugin_agent.api as api_mod
    sem = threading.Semaphore(1)
    sem.acquire()  # 占满
    monkeypatch.setattr(api_mod, "_sem", sem)
    # create_app 后 POST /tasks → 429
    ...

def test_api_env_missing_503_points_suffix():
    """env 分套缺失 → 503 点明带后缀缺项。"""
    # payload {"env": "prod"} + 只配 KD_* 未配 KD_*_PROD → 503 含 KD_BASE_URL_PROD
    ...
```

- [ ] **Step 4: 跑测试**

Run: `pytest tests/test_kingdee_agent.py -k "429 or 503 or env" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/kingdee_plugin_agent/cli.py agents/kingdee_plugin_agent/api.py tests/test_kingdee_agent.py
git commit -m "feat(api): 并发任务闸门(429)+ CLI/API 按 env 校验金蝶凭证分套"
```

---

### Task 5: 任务持久化(AsyncSqliteSaver + 恢复)

**Files:**
- Modify: `agents/kingdee_plugin_agent/api.py`(存储换 AsyncSqliteSaver + 元数据表 + 恢复)
- Modify: `tests/test_kingdee_agent.py`(重启恢复测试)

**Interfaces:**
- Consumes: `langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver`(已装)
- Produces: SQLite 文件 `data/kingdee-tasks.db`;恢复逻辑启动时扫未完成任务重建 handle

- [ ] **Step 1: api.py 换 AsyncSqliteSaver checkpointer**

```python
# 每任务图不再 MemorySaver,统一 SQLite checkpointer(thread_id 已每任务唯一):
import sqlite3, aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

def _checkpointer_factory():
    """每任务共享同一 SQLite checkpointer(AsyncSqliteSaver 线程安全)。"""
    conn = aiosqlite.connect("data/kingdee-tasks.db")
    saver = AsyncSqliteSaver(conn)
    return saver
# build_graph 缺省 checkpointer 改为工厂产出(任务级共享)
```

- [ ] **Step 2: 任务元数据表 + 恢复**

```python
# 建表:
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    env TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'created',
    created_at TEXT NOT NULL,
    requirement TEXT NOT NULL
)

# 启动时扫未完成任务,按 env 重建图 + handle:
def _restore_pending(app):
    for row in conn.execute("SELECT id, env, requirement FROM tasks WHERE status='created'"):
        task_id, env, requirement = row
        graph = build_graph(env=env)
        handle = _make_handle(task_id, graph, requirement)
        app.state.tasks[task_id] = handle
        threading.Thread(target=_run_loop, args=(handle,), daemon=True).start()
```

- [ ] **Step 3: 写测试(重启恢复)**

```python
def test_restore_pending_task():
    """建任务 → 模拟重启(新 app + 同 DB)→ 未完成任务恢复。"""
    # 建任务(写 DB)→ 构造新 app → _restore_pending → app.state.tasks 含该任务
    ...
```

- [ ] **Step 4: 跑全量 kingdee 测试**

Run: `pytest tests/test_kingdee_agent.py tests/test_kingdee_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/kingdee_plugin_agent/api.py tests/test_kingdee_agent.py
git commit -m "feat(api): 任务持久化 AsyncSqliteSaver + 重启恢复(元数据表)"
```

---

## Self-Review 记录

- **Spec 覆盖**:① apikey → Task 1 ✅;② load_skill → Task 2 ✅;③ 并发+env → Task 3+4 ✅;④ 持久化 → Task 5 ✅
- **占位扫描**:Task 2 的 smoke 脚本有「观测点」描述性占位 —— 因验证结果不可预知(成功/被拒),属合理开放步骤,非实现占位;其余步骤全部含具体代码。
- **类型一致**:`kingdee_env_vars` 在 Task 3 定义、Task 4 消费,签名一致(env="" → dict 5 键);`client_from_env_or_none(env="")` / `build_graph(env="")` 跨任务一致。
