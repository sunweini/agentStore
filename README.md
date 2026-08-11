# agentStore

基于 LangChain/LangGraph 的多步骤任务 Agent 组。

## 项目状态

**sentiment-query-agent(海外舆情检索方案生成 Agent)已交付并上线生产(10.33.17.72)**:输入一个中文公司名,自动完成 6 步流水线(实体测绘 → 主体画像 → 关键词字典 → 双轨检索式 → 属地信源 → 频次定级),产出方案组 + 组内多检索方案,经用户勾选确认后固化入库。Docker Compose 部署(API:8000/演示页:80),9 接口(含 stop/status)全链路生产实测通过;接口文档 `agents/sentiment_query_agent/API.md`,发布流程 `agents/sentiment_query_agent/deploy/README.md`。

**多用户配额与资费(v1.24.0,feature/quota-billing-stats 分支,未部署)**:apikey 即用户,免费/付费额度(commit 扣减,先免费后付费),apikey 管理(创建/修改/删除),管理员可查全部/调额度,8 新接口,MySQL 存储。设计: `docs/superpowers/specs/2026-08-11-quota-billing-stats-design.md`。

**kingdee-plugin-agent(金蝶云星空插件开发 Agent)已交付**:输入自然语言需求,自动完成 澄清 → 设计 → 生成 → 审查 → 编译修复 → 冒烟 → 打包 → 沉淀 全流程(1 主管 + 8 worker 的 LangGraph 循环图,interrupt 交互澄清 + Send 并行派发),交付可部署的插件交付包(源码 + DLL + 部署说明 + 设计/审查/需求版本记录);失败时经 w6_fail 产出"未完成"包(部分产物 + 编译错误 + 审查意见 + 原因)。CLI + Web API(SSE 实时进度)+ 演示页已跑通全流程,272 项测试全过。

## 目录结构

```
.
├── common/                  # 共享层:LLM 工厂、配置、prompt 加载、OTel
│   ├── llm.py               # 多供应商模型工厂(DeepSeek 经 ChatOpenAI)
│   ├── config.py            # 配置加载(.env)
│   ├── prompts.py           # prompt 加载
│   ├── otel.py              # OpenTelemetry 全链路初始化
│   └── skills/              # 公共 skill(所有 agent 可访问,预留)
├── agents/
│   ├── sentiment-query-agent/              # 海外舆情检索方案生成 Agent
│   │   ├── agent.py         # 图构建:6 步流水线 + AsyncSqliteSaver
│   │   ├── api.py           # FastAPI:提交/进度/方案/勾选/入库/导出
│   │   ├── auth.py          # apikey 鉴权 + 资源归属校验
│   │   ├── billing.py       # 计费:创建记 pending,commit 转正式
│   │   ├── graph/           # state / nodes(6 步)/ flows
│   │   ├── skills/          # overseas-sentiment-query-builder(agent 专属)
│   │   │   └── .../scripts/ # step1..6 分步脚本 + build_task_xlsx.py
│   │   ├── tools/           # websearch 池(gateway MCP:brave/tavily/serpapi)
│   │   ├── store/           # JSON 文件库 + 导出转换层
│   │   └── CLAUDE.md        # 本 agent 开发指南
│   └── kingdee_plugin_agent/               # 金蝶云星空插件开发 Agent
│       ├── agent.py         # 主管 + 8 worker 循环图(w6_fail 失败收尾节点)
│       ├── cli.py / api.py  # CLI 入口 / FastAPI(SSE + 澄清应答 + 验收 + 反馈端点)
│       ├── graph/           # state(任务契约 + 指标)/ supervisor / workers(w1..w7)
│       ├── skills/          # 6 个方法论 skill(load_skill 渐进式披露)
│       ├── tools/           # 编译客户端 / 金蝶 API / 冒烟 / 打包
│       ├── templates/       # 三类型插件模板(bill / service / list)
│       ├── store/ seed/     # 产物落盘 / 经验库种子
│       └── CLAUDE.md        # 本 agent 开发指南
├── compile_service/         # 编译容器(HTTP 服务:mock / msbuild 双后端)
├── web/
│   ├── demo.html            # 前端演示页(sentiment:6 步实时回显 + 勾选入库导出)
│   └── kingdee-demo.html    # kingdee 演示页(澄清流 + 任务矩阵 + 验收)
├── tests/                   # pytest(272 个测试:kingdee 全套 + sentiment)
├── docs/
│   ├── dev-standards.md     # 开发规范(含 §7 通用开发经验)
│   ├── kingdee-plugin-agent/ # kingdee 三件套:project / tech / manual
│   └── superpowers/specs/   # 设计文档
├── .env.example             # 环境变量模板
├── requirements.txt
├── pyproject.toml
└── langgraph.json           # LangGraph 配置(agent 注册)
```

## 快速开始

```bash
cp .env.example .env        # 填 DEEPSEEK_API_KEY + MCP_GATEWAY_TOKEN + API_KEYS_JSON
pip install -r requirements.txt

# 启动 API(sentiment-query-agent 舆情方案生成)
uvicorn agents.sentiment-query-agent.api:app --reload

# 前端演示页(另一个终端)
python3 -m http.server 8080 --directory web
# 浏览器打开 http://127.0.0.1:8080/demo.html

# ── kingdee-plugin-agent(金蝶插件开发)──
# 编译服务容器(mock 后端也可直接跑,见 CLAUDE.md;注意与 sentiment API 同为 8000 端口,择一启动)
docker-compose up -d

# 启动 kingdee API(SSE 实时进度)
uvicorn "agents.kingdee_plugin_agent.api:create_app" --factory --reload

# 或 CLI 直接跑(需求文本 + 目标环境)
python -m agents.kingdee_plugin_agent.cli "给采购单审核加库存校验" --env test

# kingdee 演示页(与 demo.html 同一静态服务)
# 浏览器打开 http://127.0.0.1:8080/kingdee-demo.html
```

调用示例:

```bash
# 提交任务(apikey 见 .env 的 API_KEYS_JSON)
curl -X POST http://127.0.0.1:8000/api/v1/groups \
  -H "Authorization: Bearer sk-demo-key" \
  -H "Content-Type: application/json" \
  -d '{"company_name": "中国十五冶金建设集团有限公司"}'

# kingdee 建任务(apikey 见 .env 的 KINGDEE_API_KEY;env 缺省 "test",记录进 state.environment)
curl -X POST http://127.0.0.1:8000/tasks \
  -H "X-API-Key: <KINGDEE_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"requirement": "给采购订单审核加库存校验", "env": "test"}'
```

## 测试

```bash
pytest tests/   # 272 个测试:kingdee 图全链路/CLI/API/RAG/模板/编译服务/eval 集 + sentiment 技能脚本
```

## 文档

- [版本更新说明](CHANGELOG.md)(当前 v1.23.0:sentiment 生产部署/stop/status 接口 + kingdee 系列)
- [AI Agent 开发规范](docs/dev-standards.md)(所有 agent 开发必须依据 LangChain 官方文档与 API 指引;§7 通用开发经验必读)
- [API 接口文档](agents/sentiment_query_agent/API.md)(sentiment 9 接口:提交/进度/status/方案/勾选/stop/入库/导出 + health,全真实返回示例)
- [AI 对接规范](agents/sentiment_query_agent/INTEGRATION.md)(调用方 agent 可直接阅读的对接契约)
- [sentiment 发布流程](agents/sentiment_query_agent/deploy/README.md)(生产 10.33.17.72,deploy.sh 一键发布/回滚)
- [部署文档](docs/deployment.md)(环境要求/配置/启动/运维/常见问题)
- [Agent1 重构设计(舆情方案生成)](docs/superpowers/specs/2026-08-06-sentiment-query-agent-sentiment-query-agent-design.md)
- [Agent1 原始骨架设计](docs/superpowers/specs/2026-08-06-sentiment-query-agent-langgraph-design.md)(已被重构取代,留档)
- [kingdee-plugin-agent 设计文档](docs/superpowers/specs/2026-08-08-kingdee-plugin-agent-design.md)
- [kingdee-plugin-agent 项目文档](docs/kingdee-plugin-agent/project.md)(背景/里程碑/规划)
- [kingdee-plugin-agent 技术文档](docs/kingdee-plugin-agent/tech.md)(架构/任务契约/错误处理/部署)
- [kingdee-plugin-agent 使用手册](docs/kingdee-plugin-agent/manual.md)(环境配置/CLI/Web/FAQ)
- agent 开发指南:`agents/<agent>/CLAUDE.md`
