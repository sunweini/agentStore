# agentStore

基于 LangChain/LangGraph 的多步骤任务 Agent 组。

## 项目状态

**agent1(海外舆情检索方案生成 Agent)已交付**:输入一个中文公司名,自动完成 6 步流水线(实体测绘 → 主体画像 → 关键词字典 → 双轨检索式 → 属地信源 → 频次定级),产出方案组 + 组内多检索方案,经用户勾选确认后固化入库。前端演示页已跑通全流程。

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
│   └── agent1/              # 海外舆情检索方案生成 Agent
│       ├── agent.py         # 图构建:6 步流水线 + AsyncSqliteSaver
│       ├── api.py           # FastAPI:提交/进度/方案/勾选/入库/导出
│       ├── auth.py          # apikey 鉴权 + 资源归属校验
│       ├── billing.py       # 计费:创建记 pending,commit 转正式
│       ├── graph/           # state / nodes(6 步)/ flows
│       ├── skills/          # overseas-sentiment-query-builder(agent 专属)
│       │   └── .../scripts/ # step1..6 分步脚本 + build_task_xlsx.py
│       ├── tools/           # websearch 池(gateway MCP:brave/tavily/serpapi)
│       ├── store/           # JSON 文件库 + 导出转换层
│       └── CLAUDE.md        # 本 agent 开发指南
├── web/
│   └── demo.html            # 前端演示页(6 步实时回显 + 勾选入库导出)
├── tests/                   # pytest(12 个测试)
├── docs/
│   ├── dev-standards.md     # 开发规范(含 §7 通用开发经验)
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

# 启动 API(agent1 舆情方案生成)
uvicorn agents.agent1.api:app --reload

# 前端演示页(另一个终端)
python3 -m http.server 8080 --directory web
# 浏览器打开 http://127.0.0.1:8080/demo.html
```

调用示例:

```bash
# 提交任务(apikey 见 .env 的 API_KEYS_JSON)
curl -X POST http://127.0.0.1:8000/api/v1/groups \
  -H "Authorization: Bearer sk-demo-key" \
  -H "Content-Type: application/json" \
  -d '{"company_name": "中国十五冶金建设集团有限公司"}'
```

## 测试

```bash
pytest tests/   # 12 个测试:skill 脚本/数据模型/鉴权/计费/路径
```

## 文档

- [AI Agent 开发规范](docs/dev-standards.md)(所有 agent 开发必须依据 LangChain 官方文档与 API 指引;§7 通用开发经验必读)
- [API 接口文档](docs/api.md)(7 个接口:提交/进度/方案/勾选/入库/导出 + 错误码)
- [部署文档](docs/deployment.md)(环境要求/配置/启动/运维/常见问题)
- [Agent1 重构设计(舆情方案生成)](docs/superpowers/specs/2026-08-06-agent1-sentiment-query-agent-design.md)
- [Agent1 原始骨架设计](docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md)(已被重构取代,留档)
- agent 开发指南:`agents/<agent>/CLAUDE.md`
