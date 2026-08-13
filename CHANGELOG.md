# 版本更新说明(CHANGELOG)

项目:agentStore — 基于 LangChain/LangGraph 的多步骤任务 Agent 组
仓库:https://github.com/sunweini/agentStore

---

## Agent 索引

| Agent | 当前版本 | CHANGELOG |
|---|---|---|
| sentiment-query-agent | v1.24.0 | [CHANGELOG](agents/sentiment_query_agent/CHANGELOG.md) |
| kingdee-plugin-agent | v1.26.0 | [CHANGELOG](agents/kingdee_plugin_agent/CHANGELOG.md) |

> 每 agent 独立版本号序列,撞号消除。改动归属哪个 agent → 更新该 agent 的
> CHANGELOG + bump 该 agent 版本号;纯项目级 → 本文件「项目级变更」区。

## 项目级变更

跨 agent / 公共层变更记这里:
- common/ 公共库(config/llm/rag/otel/db 等)
- compile_service(kingdee 用但属公共基建)
- 依赖升级 / 工作流约定 / 基建

## 项目级历史

### 2026-08-13(common/db.py 加 contract 独立计费表)

#### 变更

- `common/db.py init_tables()` 追加建 `contract_api_keys` / `contract_billing_records`
  两表(contract-review-agent 独立计费/鉴权用,与 sentiment 的 api_keys/billing_records
  完全隔离;生产 MySQL 建表走 deploy/init_tables.sql)

### 2026-08-13(contract-review-agent 收尾:依赖 + 测试环境 + 部署前置)

#### 变更

- `requirements.txt` 加 `python-docx` / `pypdf`(contract-review-agent docx/pdf 文件解析)
- `tests/conftest.py` 嵌入 provider:EMBEDDING_* 跟随 .env 注入 os.environ + 清
  `_embedding_model` 的 lru_cache(测试环境与生产一致,用 openai-compatible 远程嵌入
  Qwen3-Embedding-8B,不靠 load_dotenv 顺序)
- **contract-review-agent 部署前置**(套件在 `agents/contract_review_agent/deploy/`):
  生产需建 `contract_` 两表(init_tables.sql)+ .env 配 MYSQL_URL / ADMIN_APIKEY /
  BAIDU_OCR_API_KEY / BAIDU_OCR_SECRET_KEY / EMBEDDING_*;详见 deploy/README.md

### v0.1.0 — 2026-08-06(项目初始化)

#### 新增

- 项目骨架:common 共享层(LLM 工厂 / 配置 / prompt 加载)+ agents/agent1 通用骨架(占位文档)
- 多供应商模型工厂:供应商注册表,换供应商改 `.env` 不改代码
- CLAUDE.md 项目指南 + 开发规范(必须依据 langchain MCP 文档/API 开发)
- 每个 agent 独立 CLAUDE.md 约定(§6 模板)

#### 文档

- 设计文档:agent1 目录架构设计(LangGraph)
- 开发规范初版:开发铁律 / 架构约定 / 开发流程 / CLAUDE.md 模板

(自 v0.1.0 后,agent 功能版本全归各 agent CHANGELOG,根文件仅记项目级。)
