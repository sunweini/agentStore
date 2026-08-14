# 版本更新说明(CHANGELOG)

项目:agentStore — 基于 LangChain/LangGraph 的多步骤任务 Agent 组
仓库:https://github.com/sunweini/agentStore

---

## Agent 索引

| Agent | 当前版本 | CHANGELOG |
|---|---|---|
| sentiment-query-agent | v1.27.0 | [CHANGELOG](agents/sentiment_query_agent/CHANGELOG.md) |
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

### 2026-08-14(管理控制台 admin_console)

#### 变更
- 管理控制台(admin_console):跨 agent apikey 管理/角色切换/额度/报表(summary+按天 committed 趋势),`uvicorn common.admin_api:app` 起,`web/admin.html` 单文件三 tab。复用 common 计费组件,超级管理员(ADMIN_APIKEY)专用。
- common/auth.py:新 `is_super_admin`。
- common/apikey_mgmt.py:`create_apikey` 加额度参数、新 `set_role/list_keys/list_agents`、`deactivate_apikey` 超管放行。
- common/billing.py:新 `report_summary`(仅 active)/`report_history`(committed 按天)。

### 2026-08-14(公共计费硬化 M1-M8)

#### 变更
- `common/auth.py` `assert_owner` 加 agent 参数,管理员判定 per-agent(跨 agent 不放行)。
- `common/apikey_mgmt.py` ensure_admin 自动生成 key 日志脱敏(前7***后4,凭据不落明文)。
- `common/billing.py`:`commit` 前置 SELECT 与两条 UPDATE 均加 `(apikey, agent, bill_no)` 三重过滤(防跨 apikey 扣费);事务内双耗尽 guard(免费+付费用完 → 403,事务回滚防超扣);`usage_all` 加 `ORDER BY agent, apikey`(排序确定)。
- `scripts/migrate_billing.py`:校验改逐条子集(部署后新增行不误报);迁移补 `created_at`/`committed_at`。
- sentiment api.py `commit_group` 计费失败 try/except(HTTPException 原样/其他 503,error_type 不泄露)。

### 2026-08-14(新 agent 接入公共计费指引)

#### 变更
- `docs/dev-standards.md` 新增 §8「新 agent 接入公共计费组件」:接入步骤(定 agent 短名
  → api.py 用 common.billing/auth/apikey_mgmt 传 agent 参数 → 计费时机 create_pending/
  commit/失败 cancel → ensure_admin → 建表)+ 验证方式(公共单测 + agent 接口 + 全量回归)。
- 根 `CLAUDE.md` 开发流程加第 3 步:新 agent 必须接入公共计费,禁止新建独立计费文件,
  公共组件零改动(agent 只是参数)。
- 目标:新 agent 开发自动适配公共计费,不再重复调整公共组件。

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
