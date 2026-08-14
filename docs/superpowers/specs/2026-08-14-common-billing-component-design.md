# 公共计费组件设计

- 日期:2026-08-14
- 状态:设计定稿,待用户 review
- 组件:`common/billing.py` + `common/apikey_mgmt.py` + `common/auth.py`

## 1. 背景与目标

sentiment-query-agent 与 contract-review-agent 各自实现了一套**逐字相同**的计费逻辑
(扣费状态机 pending→committed/cancelled、先免费后付费、事务原子、pending 上限 5),
仅表名与业务键列名不同(group_id vs task_id)。重复维护两份,新增 agent 需再复制。

目标:抽成 `common/` 公共组件,后续所有 agent 用**一套计费逻辑**,额度按
`(apikey, agent)` 区分 —— 同一 apikey 在每个 agent 有独立额度行。

## 2. 关键决策记录

- 表结构:**单表收敛**(新建统一表,不动 sentiment 生产老表,避免改生产表风险)。
- apikey 语义:**每 agent 独立额度**(复合主键 apikey+agent)。
- 失败路径:**统一一律 cancel_pending**(采纳 contract 严谨语义,补 sentiment
  "流水线失败不释放 pending 占满并发槽位" 漏洞)。用户已确认接受。
- cancel_pending:**带 apikey+agent 过滤**(采纳 contract 更严谨实现)。
- 管理接口:**现有接口一律不变**(生产已上线),新增**单独**全局账单接口。
- 存量迁移:迁移脚本 dry-run 先行,sentiment 生产数据迁入,幂等。

## 2.5 影响面与兼容性保证(原 agent 不受影响)

**三不变**:
1. **接口表面不变**:sentiment/contract 现有端点、请求参数、响应结构**零变化**
   (sentiment 已生产,INTEGRATION.md 对接方不破坏)。
2. **存量 apikey 继续可用**:sentiment 老 `api_keys` 行迁移到
   `agent_api_keys(apikey, agent='sentiment')`,额度(含管理员
   `sk-demo-hefangyuan20260810` / 99999999)继承 → 现有用户无感。
3. **存量数据零丢失**:`billing_records` 流水迁移到 `agent_billing_records`
   (agent='sentiment', bill_no=group_id),pending/committed 状态保留。

**行为变化(2 项,均已确认)**:
1. 流水线失败路径补 cancel_pending(sentiment 原先失败不释放 pending,统一后自动释放)。
2. apikey 停用规则统一为 contract 版:管理员可停用任何 apikey(含 admin 目标),
   仅不可停用自己。sentiment 原"admin 不可删(403)"放宽为可停用 —— 能清理违规/误建
   admin;`ensure_admin` 幂等兜底,全停也可经 ADMIN_APIKEY 重建。

**管理员引导兼容**:公共 `ensure_admin(agent)` 对 sentiment 沿用现有机制 —
读 `.env` 的 `ADMIN_APIKEY`,首次启动写入 `agent_api_keys(apikey, agent='sentiment')`
管理员行(额度 99999999)。contract 无 ADMIN_APIKEY 时自动生成管理员并记录到日志。

**迁移校验与回滚**:
- 迁移后校验:对比老表/新表行数 + 每 apikey 额度四元组(free/paid quota/used)
  完全一致,不一致报错中止。
- 回滚路径:老表 `api_keys`/`billing_records` 保留不动(§10),切表失败可指回老表。

## 3. 统一表结构(新建)

### `agent_api_keys`(额度,每 agent 独立)

| 列 | 类型(MySQL / SQLite) | 说明 |
|---|---|---|
| apikey | VARCHAR(128) | 复合主键 |
| agent | VARCHAR(64) | 复合主键,agent 名(sentiment/contract/...) |
| role | ENUM('admin','normal') / VARCHAR(10) | 默认 normal |
| status | ENUM('active','deleted') / VARCHAR(10) | 默认 active |
| free_quota | INT NOT NULL DEFAULT 10 | 免费额度 |
| paid_quota | INT NOT NULL DEFAULT 0 | 付费额度 |
| free_used | INT NOT NULL DEFAULT 0 | |
| paid_used | INT NOT NULL DEFAULT 0 | |
| created_at | DATETIME DEFAULT CURRENT_TIMESTAMP | |
| updated_at | MySQL ON UPDATE CURRENT_TIMESTAMP | SQLite 无 |

主键:`PRIMARY KEY (apikey, agent)`

### `agent_billing_records`(流水,按 agent 区分)

| 列 | 类型 | 说明 |
|---|---|---|
| id | BIGINT AUTO_INCREMENT / INTEGER AUTOINCREMENT | |
| apikey | VARCHAR(128) NOT NULL | |
| agent | VARCHAR(64) NOT NULL | agent 名 |
| bill_no | VARCHAR(64) NOT NULL | 业务单号(原 group_id/task_id 收敛) |
| status | ENUM('pending','committed','cancelled') / VARCHAR(10) | 默认 pending |
| quota_type | ENUM('free','paid') NULL / VARCHAR(10) NULL | |
| created_at | DATETIME DEFAULT CURRENT_TIMESTAMP | |
| committed_at | DATETIME NULL | |

唯一:`UNIQUE KEY uk_bill (agent, bill_no)`
索引:`idx_apikey`、`idx_status`

## 4. 公共组件 API

### `common/billing.py`

| 函数 | 签名 | 语义 |
|---|---|---|
| `check_quota` | `(apikey, agent) -> None` | free+paid 剩余 ≤0 → 403 |
| `create_pending` | `(apikey, agent, bill_no) -> None` | 该 (apikey, agent) pending ≥5 → 429 |
| `commit` | `(apikey, agent, bill_no) -> None` | 事务:pending→committed,先免费后付费,quota_type 回写;无 pending → 404 |
| `cancel_pending` | `(apikey, agent, bill_no) -> None` | 带 apikey+agent 过滤,status→cancelled |
| `usage` | `(apikey, agent) -> dict` | free/paid total/used/remaining + pending_count |
| `usage_all` | `(agent=None) -> list[dict]` | 管理员:所有/指定 agent 普通用户额度 |
| `add_free_quota` / `add_paid_quota` | `(apikey, agent, count)` | 管理员充值 |
| `list_pending` | `(apikey, agent) -> list[dict]` | 待完成任务 |

### `common/apikey_mgmt.py`

| 函数 | 语义 |
|---|---|
| `create_apikey(agent, name, role="normal")` | 服务端随机 `sk-`+token,role 白名单 normal/admin,非法 ValueError |
| `update_apikey(agent, old, new)` | 换 key:额度继承 + 流水迁移(apikey 重写) |
| `deactivate_apikey(agent, apikey, admin)` | 软删;统一 contract 规则:admin 可停用任何 apikey(含 admin 目标),仅不可停用自己(403);require_admin 授权 |
| `ensure_admin(agent)` | 每 agent 首个管理员引导(额度 99999999),幂等;读 .env `ADMIN_APIKEY`(sentiment 兼容)/自动生成(contract) |

### `common/auth.py`

| 函数 | 语义 |
|---|---|
| `check_apikey(apikey, agent) -> dict` | 无效/删除 → 401 |
| `require_admin(apikey, agent)` | 非 admin → 403 |
| `assert_owner(user, agent, owner)` | 资源归属,管理员放行 |

## 5. 存量迁移(`scripts/migrate_billing.py`)

- 只迁 sentiment 生产数据(sentiment 已上生产 10.33.17.72,agentstore 库):
  - `api_keys` → `agent_api_keys`(`agent='sentiment'`)
  - `billing_records` → `agent_billing_records`(`agent='sentiment'`, `bill_no=group_id`)
- contract 无生产数据,测试数据弃(测试重导)。
- 幂等:`INSERT ... ON DUPLICATE KEY UPDATE` 或先查重。dry-run 默认先行。
- 迁移后 sentiment 接口切到新表,老表保留(可后续删)。

## 6. agent 接入

- sentiment `api.py` / contract `api.py`:import 改指 `common.billing` / `common.apikey_mgmt` / `common.auth`,调用点传 `agent='sentiment'` / `'contract'`。
- **接口表面不变**:端点、参数、返回结构不破坏现有对接方(sentiment INTEGRATION.md 对接方)。
- 删除各自 `billing.py` / `apikey_mgmt.py` / `auth.py`(或改薄转发,二选一;倾向删除+测试改指 common)。
- sentiment 失败路径补 cancel(api.py runner 异常路径调 `cancel_pending`)。

## 7. 管理接口(现有不动,新增全局账单接口)

- **现有查看/管理接口保持原样**:sentiment 的 usage/充值/apikey 管理、contract 的管理接口,
  端点/参数/返回**一律不变**(生产已上线,对接方不破坏)。计费逻辑切到 common 组件,
  但接口表面零变化。
- **新增单独接口:查看所有 agent 账单**(管理员全局),如
  `GET /api/v1/billing/usage_all`(agent 维度汇总):每个 agent 的 apikey/额度/用量。
  独立新端点,不动现有查看接口。
- 每 agent `ensure_admin` 引导首个管理员。
- apikey 创建/更新/删除:按 agent 传参,现有端点签名不变。

## 8. 测试策略

| 测试 | 覆盖 |
|---|---|
| 公共计费单测 | check_quota 403 / create_pending 429 / commit 事务先免费后付费 / 失败 cancel / 双后端(SQLite) |
| apikey_mgmt 单测 | 创建(role 白名单)/ 更新(流水迁移)/ 软删 / ensure_admin 幂等 |
| auth 单测 | 401 / 403 / assert_owner |
| 迁移脚本 | dry-run 幂等,存量行正确映射 agent/bill_no |
| sentiment 回归 | 接口表面不变(现 272 测试中的计费相关) |
| contract 回归 | 现 48 测试计费相关改指 common |

## 9. 目录/文件变更

- 新建:`common/billing.py`、`common/apikey_mgmt.py`、`common/auth.py`、`scripts/migrate_billing.py`
- 修改:`common/db.py`(`init_tables()` 加 agent_ 两表)、`agents/sentiment_query_agent/api.py`、`agents/contract_review_agent/api.py`、两 agent 的 `deploy/init_tables.sql`
- 删除:`agents/sentiment_query_agent/billing.py`、`apikey_mgmt.py`、`auth.py`;`agents/contract_review_agent/billing.py`、`apikey_mgmt.py`、`auth.py`
- 测试:`tests/test_common_billing.py`(新增)、`tests/test_sentiment_query_agent.py`、`tests/test_contract_review_agent.py`(改指 common)
- 文档:两 agent CLAUDE.md / API.md 更新、根 CHANGELOG 项目级区

## 10. 明确不做(第一版)

- 老表(`api_keys`/`billing_records`)物理删除(保留,后续清理)
- 跨 agent 共享额度(已定:每 agent 独立)
- 计费单位差异化(统一按次/按单,1 单位)
- 多租户/组织级配额
