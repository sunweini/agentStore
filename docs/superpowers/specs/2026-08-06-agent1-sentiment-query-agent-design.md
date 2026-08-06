# Agent1 重构设计:海外舆情检索方案生成 Agent

日期:2026-08-06
状态:已批准

## 1. 背景与目标

- 原 agent1 是通用骨架(占位工具),现重构为第一个真实业务 agent:**海外舆情检索方案生成 Agent**。
- 用户只输入一个中文公司名,系统自动完成 6 步流水线,产出方案组 + 组内多方案,用户勾选确认后固化入库。
- 基于 LangChain/LangGraph,全程依据 langchain MCP 文档/API 开发(见 docs/dev-standards.md)。
- 重构不新建目录,直接替换 `agents/agent1/` 内容,`langgraph.json` 注册名不变。

## 2. 技术选型

| 项 | 选择 |
|---|---|
| 编排 | LangGraph 状态机 + SqliteSaver checkpointer(中断/续跑) |
| 交互层 | FastAPI(纯 API 接口,勾选是外部事件不塞图) |
| LLM | DeepSeek(common/llm.py 工厂) |
| 搜索 | gateway MCP websearch 池(brave/tavily/serpapi 三引擎,MultiServerMCPClient) |
| 知识 | overseas-sentiment-query-builder skill(项目内 agents/agent1/skills/) |
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

每步统一模式:websearch 搜索 → LLM 按 skill 格式生成(JSON schema)→ 校验写回状态。

## 4. 目录结构

```
agents/agent1/
├── CLAUDE.md                    # 按 dev-standards §6 模板
├── __init__.py
├── agent.py                     # 图构建:6 步流水线 + SqliteSaver
├── api.py                       # FastAPI:提交/进度/方案/勾选/入库/导出
├── auth.py                      # apikey 鉴权 + 计费
├── graph/
│   ├── __init__.py
│   ├── state.py                 # 数据模型:方案组/方案/轨/步骤状态
│   ├── nodes.py                 # 6 步节点(websearch → LLM → 格式校验)
│   └── flows.py                 # 图构建:顺序边 + 中断路由
├── skills/
│   ├── __init__.py
│   ├── loader.py                # skill 指令加载(项目内路径)
│   └── overseas-sentiment-query-builder/   # ★ 项目内 skill(agent1 专属)
│       ├── SKILL.md
│       ├── references/  assets/  scripts/
├── prompts/
│   ├── step1.md … step6.md      # 6 步节点 prompt(嵌入 skill 规则)
├── tools/
│   ├── __init__.py
│   └── websearch.py             # gateway MCP 池封装(3 引擎自动切换)
├── store/
│   ├── __init__.py
│   └── scheme_store.py          # JSON 文件库(草稿/正式/索引)
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
- 任务状态:6 步每步 pending/running/done/error,存 checkpointer。

## 6. Skill 目录策略(用户指定)

- `common/skills/` — 公共 skill,所有 agent 可访问。
- `agents/<agent>/skills/` — 仅该 agent 可用。
- overseas-sentiment-query-builder 放 `agents/agent1/skills/`(从 ~/.claude/skills/ 复制)。
- loader 按 agent → common 顺序查找。

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

## 8. 鉴权与计费

- apikey:`Authorization: Bearer <apikey>`,合法 key 列表配 `.env`/JSON 文件,标识用户。
- 计费:一次完整流程(提交 → 6 步 → 确认入库)= 1 计费单位。commit 时记一条到 `data/billing/<user>.json`。
- 进度查询/勾选不重复计费。

## 9. OpenTelemetry 全链路(已写入 dev-standards §5)

- `common/otel.py`:统一 OTLP exporter 初始化,FastAPI middleware 全请求 trace。
- LangChain 原生 LLM span + 图节点手动 span + MCP 工具 span。
- 分层:API 请求 → 图执行(thread_id)→ 节点 span(websearch/LLM)。
- 已加入开发规范:所有 agent 必须接 OTel。

## 10. 错误处理

- 单步失败 → 标 error,可单独重跑。
- MCP 失败 → 切引擎重试(3 引擎池)。
- LLM 格式错 → 重试 2 次,仍错标 error + GAP。
- 无 key/无 MCP → 明确报错。

## 11. 依赖新增

fastapi / uvicorn / langchain-mcp-adapters / opentelemetry-sdk / opentelemetry-exporter-otlp / opentelemetry-instrumentation-fastapi / openpyxl

## 12. 测试

- skill loader 单测 / websearch 池单测(mock)/ 数据模型单测 / 图单测(mock LLM+websearch)/ API 单测(鉴权/计费/入库)/ 端到端(有 key+MCP)。

## 13. 范围外

账户系统(apikey 替代)、Web 前端(外部系统接 API)、爬虫调度器(方案生成后对接)。

## 14. 实施步骤

1. 复制 skill 到 agents/agent1/skills/
2. 删原骨架(agent.py/state/nodes/tools/system.md),建新结构
3. common/otel.py
4. graph(state/nodes/flows)+ agent.py + 依赖
5. tools/websearch.py + skills/loader.py + prompts/
6. api.py + auth.py + store/
7. 测试
8. CLAUDE.md 模板重写
