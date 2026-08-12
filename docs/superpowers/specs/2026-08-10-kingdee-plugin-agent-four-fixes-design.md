# kingdee-plugin-agent 四项改造设计(apikey / load_skill / 并发+env / 持久化)

日期:2026-08-10
状态:已确认(用户批准顺序方案:1 apikey → 2 load_skill 验证 → 3 并发+env → 4 持久化)

## 背景

v1 已知债务(tech.md §11 / CLAUDE.md「v1 已知债务」)四项,按成本升序推进:

1. apikey 非 timing-safe(`x_api_key != effective_key` 直接字符串比较)
2. load_skill 绑定未对真实 DeepSeek 验证(决定 w1-w5 LLM 调用路径)
3. API 线程无并发上限 + `--env` 未做环境级差异化(凭证未按环境分套)
4. 内存任务存储重启即丢(无持久化/恢复)

## 1. apikey timing-safe(无风险,5 分钟)

**现状**:`api.py::_check`(line 305-308)直接用 `x_api_key != effective_key` 字符串比较,时序侧信道可探测 apikey 前缀。

**改法**:`secrets.compare_digest` 替换。compare_digest 要求同类型(str/bytes),统一 `encode()` 后比较。

```python
import secrets
def _check(x_api_key: str) -> None:
    if not effective_key or not secrets.compare_digest(
            x_api_key.encode(), effective_key.encode()):
        raise HTTPException(401, "apikey 无效")
```

**测试**:现有 401 测试语义不变(错 key 仍 401),补 compare_digest 断言。

## 2. load_skill 线上验证(验证型,非代码改造)

**现状**:`structured_with_skill`(tools + json_schema response_format 组合绑定 load_skill)未对真实 DeepSeek 验证。

**流程**(CLAUDE.md 已写明):
1. `.env` 配 DeepSeek key(`DEEPSEEK_API_KEY`)
2. 一次性 smoke 脚本调 w1 generate_questions(真实 LLM)
3. 观测:API 拒绝?畸形 JSON?工具调用正常?
4. 被拒 → 回退 JSON Mode(`bind_tools([load_skill], strict=True).bind(response_format={"type": "json_object"})` + 手动 2 回合循环)
5. 产出 = 验证结论 + 必要回退修改,更新 CLAUDE.md

**不做代码改造**,除非验证发现需要回退。

## 3. API 并发上限 + --env 凭证分套

### 3.1 并发上限

**现状**:每任务 `threading.Thread(target=_run_loop, ...)` 无上限,流量大时线程风暴。

**改法**:`threading.Semaphore(MAX_CONCURRENT_TASKS)`(默认 4,env `KINGDEE_MAX_CONCURRENT` 可配)。**429 在 create_task 请求处理里发**(进线程前 acquire);线程入口只 release —— 线程内 raise HTTPException 到不了 FastAPI,会被吞掉。

```python
MAX_CONCURRENT_TASKS = int(config.get_env("KINGDEE_MAX_CONCURRENT", "4"))
_sem = threading.Semaphore(MAX_CONCURRENT_TASKS)

@app.post("/tasks")
def create_task(...):
    ...
    if not _sem.acquire(blocking=False):
        raise HTTPException(429, "并发任务数已达上限,稍后重试")
    threading.Thread(target=_run_loop, args=(handle,), daemon=True).start()

def _run_loop(handle):
    try:
        ... # 原逻辑
    finally:
        _sem.release()
```

信号量进程内,重启即失效(可接受,v1 语义;持久化落地后再考虑跨进程限流)。

### 3.2 --env 凭证分套

**现状**:`KD_*` 4 项单套全局,`--env` 只记 `state.environment["env_name"]`,凭证未按环境区分。

**改法**:凭证命名 `<VAR>_<ENV>`(env 大写),如 `KD_BASE_URL_TEST`。env 空 = 默认环境,直接用 `KD_*` 4 项(兼容现有单环境部署)。**`KD_LCID`(语系)同样随 env 分套**(`KD_LCID_TEST` 等),客户端构造时一并按 env 取。

**config.py 加辅助**:

```python
def kingdee_env_vars(env: str = "") -> dict:
    """按环境取金蝶凭证:优先 <VAR>_<ENV>,回落 <VAR>(默认环境)。
    含 KD_LCID(语系),客户端按 env 一并构造。"""
    prefix = f"_{env.upper()}" if env else ""
    return {name: get_env(f"{name}{prefix}") for name in
            ("KD_BASE_URL", "KD_USERNAME", "KD_PASSWORD", "KD_DATA_CENTER",
             "KD_LCID")}
```

**改动点**:
- `kingdee_api.py::client_from_env_or_none(env="")`:按 env 取凭证(含 KD_LCID)
- `agent.py::build_graph(env="")`:`api = api_client or client_from_env_or_none(env=env)`
- `api.py` 硬门槛:按 `payload["env"]` 取套,缺失 503 点明缺项(带 env 后缀名)
- `cli.py`:`--env` 已必填,硬门槛查 `kingdee_env_vars(args.env)` 对应套

**env 传图方式(简化,不用 graph_factory 回调)**:env 名在 create_task/run_cli 时才知,graph_factory 在 app 创建时定义、拿不到 env。改为 **create_task 里直接 `build_graph(env=env_name)`**(不绕 graph_factory;测试注入时 graph_factory 仍可覆盖,此时 env 由注入方自理)。CLI 同理 `build_graph(env=args.env)`。

**env 语义对齐**:CLI `--env` **保持必填**(环境明确化,防误连生产);API `payload["env"]` **可选**(空回落默认环境 `KD_*`)。两者都经 `kingdee_env_vars(env)` 取凭证。

## 4. 任务持久化(重,最后)

**现状**:`app.state.tasks` 内存 dict,重启即丢。

**改法**(设计阶段,实现时细化):
- checkpointer:MemorySaver → **AsyncSqliteSaver**(SQLite 文件,`data/kingdee-tasks.db`),任务状态可恢复
- 任务元数据表:`tasks(id, env, status, created_at, requirement)` — 恢复时重建 handle
- **注意**:TaskState/Subtask 经 msgpack 序列化,换 checkpointer 需验证字段兼容(CLAUDE.md 已注明)
- **env 交互**:恢复任务时需重建对应 env 的图 —— 任务元数据表存 env,恢复时 `build_graph(env=...)` 用**该 env 的凭证**(而非当前默认 env),否则恢复的任务连错账套

**依赖**:`langgraph-checkpoint-sqlite` 已在 requirements.txt。

**实现拆分**(后续 writing-plans):
1. AsyncSqliteSaver 替换 MemorySaver,建库/迁移
2. 任务元数据表 + 恢复逻辑(启动时扫未完成任务)
3. 测试:重启恢复、并发限流跨重启

## 验证

- 1:单测(401 + compare_digest)
- 2:真实 DeepSeek smoke,结论记录
- 3:单测(429 并发、env 凭证选择、缺失点明)
- 4:单测(重启恢复、checkpointer 兼容)

## 影响

- 1/3 仅 api.py/kingdee_api.py/agent.py/cli.py/config.py,测试对应补
- 2 仅验证,无代码(除非回退)
- 4 改 checkpointer,风险最高,最后做
