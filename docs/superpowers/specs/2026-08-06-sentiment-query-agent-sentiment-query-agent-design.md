# Sentiment-Query-Agent 重构设计:海外舆情检索方案生成 Agent

日期:2026-08-06
状态:已批准

## 1. 背景与目标

- 原 sentiment-query-agent 是通用骨架(占位工具),现重构为第一个真实业务 agent:**海外舆情检索方案生成 Agent**。
- 用户只输入一个中文公司名,系统自动完成 6 步流水线,产出方案组 + 组内多方案,用户勾选确认后固化入库。
- 基于 LangChain/LangGraph,全程依据 langchain MCP 文档/API 开发(见 docs/dev-standards.md)。
- 重构不新建目录,直接替换 `agents/sentiment-query-agent/` 内容,`langgraph.json` 注册名不变。

## 2. 技术选型

| 项 | 选择 |
|---|---|
| 编排 | LangGraph 状态机 + AsyncSqliteSaver checkpointer(中断/续跑,支持多用户并发) |
| 交互层 | FastAPI(纯 API 接口,勾选是外部事件不塞图) |
| LLM | DeepSeek(common/llm.py 工厂) |
| 搜索 | gateway MCP websearch 池(brave/tavily/serpapi 三引擎,MultiServerMCPClient) |
| 知识 | overseas-sentiment-query-builder skill(项目内 agents/sentiment-query-agent/skills/) |
| 入库 | JSON 文件库 |
| 可观测 | OpenTelemetry 全链路(common/otel.py) |

## 3. 功能流程

```
用户输入中文公司名 → 6 步流水线 → 方案组(多方案×多轨) → API 勾选确认 → 固化入库
```

6 步(对应 skill 工作流):
1. 实体测绘:母公司/海外法人/拼写变体/同名干扰
2. 主体画像:角色判定(承包商/业主)+ direct/indirect/context 相关度口径
3. 分层关键词字典:A/B/C/D/R/X 六层 + context_guard
4. 分组双轨检索式:方案×轨,布尔 + Google 双语法
5. 属地信源白名单:每轨 sources[](域名)
6. 频次与风险定级:每轨 frequency + risk

每步统一模式:websearch 搜索 → LLM 生成 → **调 skill 分步脚本**(stepN.py)按固定格式传回 → 校验写回状态。

## 4. 目录结构

```
agents/sentiment-query-agent/
├── CLAUDE.md                    # 按 dev-standards §6 模板
├── __init__.py
├── agent.py                     # 图构建:6 步流水线 + AsyncSqliteSaver
├── api.py                       # FastAPI:提交/进度/方案/勾选/入库/导出
├── auth.py                      # apikey 鉴权 + 资源归属校验
├── billing.py                   # 计费:创建 group 记 pending,commit 转正式
├── graph/
│   ├── __init__.py
│   ├── state.py                 # 数据模型:方案组/方案/轨/步骤状态
│   ├── nodes.py                 # 6 步节点(websearch → LLM → 格式校验)
│   └── flows.py                 # 图构建:顺序边 + 中断路由
├── skills/
│   ├── __init__.py
│   ├── loader.py                # load_skill 工具(渐进式披露,agent→common 查找)
│   └── overseas-sentiment-query-builder/   # ★ 项目内 skill(sentiment-query-agent 专属,已改造)
│       ├── SKILL.md
│       ├── references/          # 方法论 4 件 + output-formats.md(6 步格式契约)
│       ├── assets/              # task_spec_example.json
│       └── scripts/             # step1..6 分步脚本 + build_task_xlsx.py
├── tools/
│   ├── __init__.py
│   └── websearch.py             # gateway MCP 池封装(3 引擎自动切换)
├── store/
│   ├── __init__.py
│   ├── scheme_store.py          # JSON 文件库(草稿/正式/索引)
│   └── converter.py             # 方案组 → skill spec 格式转换(导出用)
```

公共层新增 `common/otel.py`(OTel 初始化);`common/skills/` 预留公共 skill 目录。

## 5. 数据模型

```
SchemeGroup(方案组): group_id, company_name, meta(角色/口径/地区), status, schemes[]
Scheme(方案): id(Q0..), name, region, lang, risk, frequency, relevance, desc, gaps[], tracks[]
Track(轨): key, boolean_query, google_query, sources[], selected
```

- 勾选:方案级 selected + 轨级 selected 两级。
- 汇总:勾选轨数 = 任务行数。
- 轨类型固定 6 类(key 取值):`a 全量` / `b 精准` / `c 不点名` / `快讯` / `司法` / `招标`。
- 任务状态:6 步每步 pending/running/done/error,存 checkpointer。
- **commit 后冻结**:勾选状态固化,status 置「已入库」;再改勾选须走「重新生成」新流程(新 group_id)。

### 5.1 分步格式契约(6 步 schema,存 skill references/output-formats.md)

每步脚本输出的固定 JSON 格式,字段对齐 skill 最终 spec:

| 步骤 | 脚本 | 输出格式(schema 关键字段) | 对齐 spec |
|---|---|---|---|
| 1 实体测绘 | step1_entities.py | `entities:{parent, subsidiaries[], overseas_entities[{name,lang,region}], spelling_variants[], interference_sources[]}` | —(输入层) |
| 2 主体画像 | step2_profile.py | `profile:{role(承包商/业主/ai判定), relevance_rules:{direct,indirect,context}, regions[]}` | —(输入层) |
| 3 关键词字典 | step3_keywords.py | `keywords:[{layer(A/B/C/D/R/X), category, terms, lang, guard, note}]` | spec `keywords[]` 行 |
| 4 双轨检索式 | step4_queries.py | `schemes:[{id, name, region, lang, tracks:[{key(a/b/c/快讯/司法/招标), boolean, google}]}]` | spec `tasks[]` 行(部分) |
| 5 属地信源 | step5_sources.py | 每轨补 `sources[]`(域名白名单) | spec `tasks[].sources` |
| 6 频次定级 | step6_cadence.py | 每轨补 `frequency, risk, relevance`,组装完整 task 行 | spec `tasks[]` 行(完整) |

- 步骤 4+5+6 合并 = 完整 tasks 行;步骤 3 = keywords 行;拼图式组装,导出零转换。
- 每步脚本:校验字段、标准化、补默认值、缺字段记 GAP(编号 GAP00N)。
- 格式定义唯一来源:skill `references/output-formats.md`,节点 prompt 引用它。
- **两机制职责分离**:`load_skill` 取方法论知识(SKILL.md/references,喂 LLM 理解怎么干);`stepN.py` 只做格式契约(LLM 原始输出 → 固定 JSON 标准化)。互不替代。
- **LLM 输出形式**:每步节点强制 LLM 输出 JSON(节点 prompt 规定),stepN.py 接收 JSON 做校验/补默认/记 GAP。LLM 输出非 JSON → 脚本返回格式错误,节点重试 2 次,仍错标 error + GAP。

## 6. Skill 目录策略与加载(用户指定 + 官方 skill 架构 + 分步脚本改造)

- `common/skills/` — 公共 skill,所有 agent 可访问。
- `agents/<agent>/skills/` — 仅该 agent 可用。
- overseas-sentiment-query-builder 放 `agents/sentiment-query-agent/skills/`(从 ~/.claude/skills/ 复制)。
- loader 按 agent → common 顺序查找。
- **skill 原生加载(官方 skill 架构,渐进式披露)**:agent 把 skill 打包成 `load_skill` 工具,启动只加载 skill 摘要,agent 需要时按需调 `load_skill` 取完整内容(SKILL.md + references)。不手拆 6 个 prompt 文件。官方文档: /oss/python/langchain/multi-agent/skills。
- **skill 改造:每步一个脚本,按格式传回(用户要求,路线 1)**:
  - skill 现状只有 `scripts/build_task_xlsx.py`(最终 Excel),无分步接口,不分步返回数据 → 需改造。
  - `agents/sentiment-query-agent/skills/overseas-sentiment-query-builder/scripts/` 加 6 个分步脚本:`step1_entities.py` / `step2_profile.py` / `step3_keywords.py` / `step4_queries.py` / `step5_sources.py` / `step6_cadence.py`。
  - 每个脚本 = 该步的格式契约执行器:输入上步产物 + 本步原始结果(LLM/websearch 输出),**输出固定格式 JSON**(schema 见 §5.1)。职责:校验字段、标准化、补默认值、缺字段记 GAP。
  - 每步节点:调对应脚本 → 拿格式结果 → 写 state。格式由脚本保证,LLM 自由生成,格式错由脚本兜底。
  - 6 步格式定义存 skill 的 `references/output-formats.md`(skill 自包含)。
- 第 3-6 步产物字段逐一对齐 skill 最终 spec 的 tasks 行 / keywords 行(拼图式组装,导出零转换)。
- skill 的 Excel 步骤(第 6 步)不复用,导出走 API + converter(§7)。

## 7. API 接口

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | /api/v1/groups | 提交任务(公司名/角色/地区/检索类型)→ group_id |
| GET | /api/v1/groups/{id}/progress | 查 6 步进度 |
| GET | /api/v1/groups/{id}/schemes | 获取方案组(含勾选态) |
| PUT | /api/v1/groups/{id}/selection | 提交勾选 |
| POST | /api/v1/groups/{id}/commit | 确认入库(计费点) |
| GET | /api/v1/groups/{id}/export | 导出 Excel(skill 脚本) |

- 后台 `asyncio.create_task` 跑图,`thread_id = group_id`。
- 中断/续跑:同 thread_id 重跑,已完成步骤复用(checkpointer)。
- **导出转换层**:`store/` 内 `converter.py` 把勾选后的方案组(轨)转成 skill 脚本要求的 spec 格式(tasks 行 + 关键词字典 + extra_notes),再调 skill 的 `build_task_xlsx.py` 生成 Excel。转换层放 store,复用计费/勾选冻结后的正式数据。

## 8. 鉴权与计费

- **鉴权**(`auth.py`):apikey 校验(`Authorization: Bearer <apikey>`,合法 key 列表配 `.env`/JSON 文件)+ 资源归属校验(每个 group 记录 `owner`,所有 `/groups/{id}/*` 接口校验归属,越权 403)。apikey 模型防跨用户访问。
- **计费**(`billing.py`,独立于 auth):一次完整流程 = 1 计费单位。创建 group 时记 pending 计费记录,commit 时转正式计费(1 单位);未 commit(失败/取消/过期)不计费。防刷:同一 apikey 的 pending 记录限并发数(如最多 5 个)。进度查询/勾选不重复计费。
- 计费记录:`data/billing/<user>.json`,commit 时记 `{group_id, user, created_at, committed_at}`。

## 9. OpenTelemetry 全链路(已写入 dev-standards §5)

- `common/otel.py`:统一 OTLP exporter 初始化,FastAPI middleware 全请求 trace。
- LangChain 原生 LLM span + 图节点手动 span + MCP 工具 span。
- 分层:API 请求 → 图执行(thread_id)→ 节点 span(websearch/LLM)。
- **高基数约束(遵循 OBS-CORE-003)**:用户标识(apikey)只进日志/计费,不进 span label;span 只带 trace_id/thread_id/步骤名/引擎名等低基数 label。
- 已加入开发规范:所有 agent 必须接 OTel。

## 10. 错误处理

- 单步失败 → 标 error,可单独重跑。
- MCP 失败 → 切引擎重试(3 引擎池)。
- LLM 格式错 → 重试 2 次,仍错标 error + GAP。
- 无 key/无 MCP → 明确报错。
- **MCP 连接生命周期**:应用启动时建单例连接,`get_tools()` 结果缓存复用,不在每次跑图时重建。

## 11. 依赖新增

fastapi / uvicorn / langchain-mcp-adapters / opentelemetry-sdk / opentelemetry-exporter-otlp / opentelemetry-instrumentation-fastapi / openpyxl

## 12. 测试

- skill 分步脚本单测(6 个脚本格式契约/缺字段记 GAP)/ skill loader 单测 / websearch 池单测(mock)/ 数据模型单测 / 图单测(mock LLM+websearch)/ API 单测(鉴权/资源归属校验 403/计费 pending→commit/入库冻结)/ 端到端(有 key+MCP)。

## 13. 范围外

账户系统(apikey 替代)、Web 前端(外部系统接 API)、爬虫调度器(方案生成后对接)。

## 14. 实施步骤

1. 复制 skill 到 agents/sentiment-query-agent/skills/
2. 删原骨架(agent.py/state/nodes/tools/system.md),建新结构
3. common/otel.py
4. graph(state/nodes/flows)+ agent.py + 依赖
5. tools/websearch.py + skills/loader.py(load_skill 工具)
6. api.py + auth.py + store/
7. 测试
8. CLAUDE.md 模板重写
