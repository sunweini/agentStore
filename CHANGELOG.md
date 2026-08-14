# 版本更新说明(CHANGELOG)

项目:agentStore — 基于 LangChain/LangGraph 的多步骤任务 Agent 组
仓库:https://github.com/sunweini/agentStore

---

## Agent 索引

| Agent | 当前版本 | CHANGELOG |
|---|---|---|
| sentiment-query-agent | v1.26.0 | [CHANGELOG](agents/sentiment_query_agent/CHANGELOG.md) |
| contract-review-agent | v0.8.0 | [CHANGELOG](agents/contract_review_agent/CHANGELOG.md) |
| kingdee-plugin-agent | v1.26.0 | [CHANGELOG](agents/kingdee_plugin_agent/CHANGELOG.md) |

> 每 agent 独立版本号序列,撞号消除。改动归属哪个 agent → 更新该 agent 的
> CHANGELOG + bump 该 agent 版本号;纯项目级 → 本文件「项目级变更」区。

## 项目级变更

跨 agent / 公共层变更记这里:
- common/ 公共库(config/llm/rag/otel/db 等)
- compile_service(kingdee 用但属公共基建)
- 依赖升级 / 工作流约定 / 基建

## 项目级历史

### 2026-08-14(公共计费组件:common.billing/apikey_mgmt/auth + 统一表 + 迁移脚本)

#### 变更

- **公共计费组件新建**(设计文档 `docs/superpowers/specs/2026-08-14-common-billing-component-design.md`):
  - `common/billing.py`:check_quota / create_pending / commit / cancel_pending / usage / usage_all / add_free_quota / add_paid_quota,额度按 **(apikey, agent)** 维度,先免费后付费,commit 事务原子,pending 上限 5
  - `common/apikey_mgmt.py`:create_apikey(服务端随机 key)/ update_apikey(换 key,额度/流水继承)/ deactivate_apikey(软删,统一 contract 规则:admin 目标可停用,仅"不可停用自己")/ admin_list / ensure_admin(每 agent 首个管理员引导,幂等)
  - `common/auth.py`:check_apikey / require_admin / assert_owner
- **单表收敛**:统一表 `agent_api_keys` / `agent_billing_records`((apikey, agent) 复合主键,agent_billing_records 以 bill_no 为业务单号);sentiment(agent='sentiment',bill_no=group_id)/ contract(agent='contract',bill_no=task_id) 同表同 schema,额度按 agent 维度隔离。`common/db.py init_tables()` 与两 agent `deploy/init_tables.sql` 同步追加。**老表 api_keys / billing_records / contract_* 保留不删(回滚路径)**。
- **存量迁移脚本**:`scripts/migrate_billing.py` —— api_keys / billing_records → agent_api_keys / agent_billing_records(agent='sentiment');`--dry-run` 默认先行(只统计),`--apply` 实迁 + **迁移后校验**(新表行数 == 老表 + 每 apikey 额度四元组一致);check-then-insert 幂等(重跑跳过已迁行),兼容 SQLite(测试)/MySQL(生产)双后端。
- sentiment / contract 两 agent 已接入公共组件(各自 agent 参数,接口端点/参数/返回不变),详见各 agent CHANGELOG 与 API.md。

### 2026-08-14(common/llm.py 支持请求超时)

#### 变更
- `get_chat_model(provider, model_id, timeout=None)` 新增 timeout 参数,透传
  ChatOpenAI request_timeout(None 用 OpenAI 默认 600s)。向后兼容,不影响
  sentiment/kingdee 现有调用。合同审核单章 LLM 调用传 120s 防挂起。
- 供应商构建器签名 `(model_id, timeout=None)`;注册表注解同步。

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
