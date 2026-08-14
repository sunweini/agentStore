# 计费与统计接口接入指南(AI Agent 可读)

> 本文档供后续接入的 AI agent / 开发者阅读,统一说明:
> 1. 计费模型(额度/扣减时机/状态机)
> 2. 新 agent 如何零改动接入公共计费组件
> 3. 统计/报表接口(admin 控制台 + 公共接口)
> 设计文档:`docs/superpowers/specs/2026-08-14-common-billing-component-design.md` 与 `2026-08-14-admin-console-design.md`。

## 0. 一句话总览

**apikey 即用户**,按 `(apikey, agent)` 维度记账。每个 apikey 有免费额度(初始 10)+ 付费额度(充值);提交任务记 `pending`,任务成功 `commit` 扣 1 单位(先免费后付费),失败/取消 `cancel_pending` 不扣费。统计/报表由 admin 控制台(超级管理员)+ 公共计费接口提供。

## 1. 计费模型

### 1.1 数据表(MySQL `agentstore` 库,统一两表)

- `agent_api_keys`(复合主键 `apikey + agent`):`role`(admin/normal)、`status`(active/deleted)、`free_quota/free_used/paid_quota/paid_used`。
- `agent_billing_records`(`UNIQUE(agent, bill_no)`):`status`(pending/committed/cancelled)、`quota_type`(free/paid)、`committed_at`。

### 1.2 核心语义

| 概念 | 规则 |
|---|---|
| 额度 | 免费(初始 10)+ 付费(充值);剩余 = quota - used |
| 扣减时机 | `commit`(任务成功)扣 1 单位,先免费后付费,事务原子 |
| 不计费 | 失败、停止、取消、未 commit 的任务 |
| 并发 | 同 `(apikey, agent)` pending ≤ 5,超出 429 |
| 管理员 | `role='admin'`,额度 99999999,不受归属限制 |
| 超级管理员 | `.env ADMIN_APIKEY`,可跨 agent 操作(管理控制台专用) |
| 删除 | 软删(`status='deleted'`),鉴权拒绝,数据保留 |

### 1.3 计费状态机

```
提交任务 → create_pending(status=pending)
  ├─ 成功 → commit(扣 1 单位,status=committed,记 quota_type)
  └─ 失败/取消/stop → cancel_pending(status=cancelled,不扣费)
```

**铁律**:失败/异常/取消路径必须 `cancel_pending` 释放 pending 槽位,否则泄漏并发额度(公共组件已统一实现,接入方只需在 finally 调)。

## 2. 新 agent 接入(零改动公共组件)

详见 `docs/dev-standards.md §8`。摘要:

1. 定 agent 短名(小写英文,如 `myagent`)。
2. `api.py` import 公共组件,传 agent 参数:
   ```python
   from common import auth, billing, apikey_mgmt
   # 鉴权
   auth.check_apikey(apikey, "myagent")        # 401 无效
   auth.require_admin(apikey, "myagent")       # 403 非管理员
   # 计费
   billing.check_quota(apikey, "myagent")      # 额度≤0 → 403
   billing.create_pending(apikey, "myagent", bill_no)  # pending≥5 → 429
   billing.commit(apikey, "myagent", bill_no)          # 完成扣 1
   billing.cancel_pending(apikey, "myagent", bill_no)  # 失败/取消释放
   billing.usage(apikey, "myagent")            # 余额查询
   # apikey 管理
   apikey_mgmt.create_apikey("myagent", name, role)
   apikey_mgmt.deactivate_apikey("myagent", apikey, admin)
   apikey_mgmt.ensure_admin("myagent")         # 启动时引导管理员
   ```
3. 计费时机:提交 → `create_pending`;成功 → `commit`;失败/取消 → `cancel_pending`。
4. 启动调 `ensure_admin("myagent")`(幂等,读 `.env ADMIN_APIKEY`)。
5. 建表:本地 `db.init_tables()` 已建 agent_* 两表;生产 `deploy/init_tables.sql` 含两表(照抄 sentiment/contract DDL)。

**禁止**:新建独立计费文件、改公共组件签名、加 if-agent 分支。接口端点自定,内部计费走公共组件。

## 3. 统计/报表接口

### 3.1 admin 控制台(超级管理员,推荐)

- 页面:http://10.33.17.72/admin/(登录用 `.env ADMIN_APIKEY`)
- 后端:`common/admin_api.py`,前缀 `/api/v1/admin/`,鉴权 `Authorization: Bearer <ADMIN_APIKEY>`。

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/api/v1/admin/agents` | agent 列表(附 active key 数) |
| GET | `/api/v1/admin/apikeys?agent=` | 跨 agent 全量 apikey(含 admin/软删) |
| POST | `/api/v1/admin/apikeys` | 创建 apikey(`{agent, role, free_quota?, paid_quota?}`) |
| PATCH | `/api/v1/admin/apikeys` | 改角色(`{apikey, agent, role}`) |
| PUT | `/api/v1/admin/apikeys` | 换 key(`{apikey, agent, new_apikey}`) |
| DELETE | `/api/v1/admin/apikeys` | 软删(`{apikey, agent}`) |
| POST | `/api/v1/admin/apikeys/quota` | 增额度(`{apikey, agent, type: free\|paid, count}`) |
| GET | `/api/v1/admin/report/summary` | 按 agent 汇总(仅 active normal key) |
| GET | `/api/v1/admin/report/history?agent=&days=` | 按天 committed 扣费趋势 |

**报表口径**:
- `report/summary`:只汇总 `status='active' AND role='normal'`(排除 admin 的 99999999 占位额度),`{agents:[{agent,key_count,free_used,free_remaining,paid_used,paid_remaining}], total:{...}}`。
- `report/history`:只统计 `committed`,`GROUP BY DATE(committed_at), agent`,`days` 默认 30(1-365),返回 `{series:[{date,agent,committed}]}`。

**安全**:所有 key 定向操作 **body 传参**(key 不进 URL,防 access log 泄露);日志 apikey 一律 `_mask_apikey` 脱敏。

### 3.2 各 agent 自带资费接口(管理员维度)

- **sentiment**(agent='sentiment'):
  - `GET /api/v1/billing/usage` — 当前 apikey 资费(普通查自己/管理员查全部)
  - `GET /api/v1/billing/usage_all?agent=` — 全局账单(管理员,跨 agent)
  - `POST /api/v1/billing/quota/{paid,free}` — 增减额度(管理员)
- **contract**(agent='contract'):
  - `GET /api/v1/apikeys`(header `apikey: <管理员key>`)— 该 agent 全部 key 额度列表

> 注意:各 agent 资费接口鉴权方式不同(sentiment 用 `Authorization: Bearer`,contract 用 header `apikey`),对接时看各 agent 的 `INTEGRATION.md`。

### 3.3 公共组件(代码层,供 agent 内部调用)

```python
billing.usage_all(agent=None)   # 跨 agent 每 key 额度(role=normal, status=active)
apikey_mgmt.list_keys(agent=None)  # 跨 agent 全量(含 admin/软删)
apikey_mgmt.list_agents()          # agent 列表 + active key 数
billing.report_summary(agent=None) # 按 agent 汇总(仅 active normal)
billing.report_history(agent=None, apikey=None, days=30)  # 按天 committed
```

## 4. 已有 agent 接入现状

| agent | 短名 | 端口 | 鉴权 header | bill_no 语义 | 计费时机 |
|---|---|---|---|---|---|
| sentiment-query-agent | `sentiment` | 8000 | `Authorization: Bearer` | group_id | commit(勾选入库) |
| contract-review-agent | `contract` | 8002 | `apikey` | task_id | commit(审核 done) |

## 5. 接入自检清单

新 agent 接完后对照验证(见 dev-standards §8.2):

- [ ] 无/无效 apikey → 401
- [ ] 非管理员操作管理接口 → 403
- [ ] 额度耗尽 → 403
- [ ] 同 (apikey, agent) pending ≥5 → 429
- [ ] 提交 create_pending → 成功 commit 扣 1 → usage 正确
- [ ] 失败/取消 → cancel_pending → pending_count 归零
- [ ] 全量回归 `pytest tests/ -q` 通过
- [ ] admin 控制台 `/admin/` 能查到该 agent 的 apikey + 报表
