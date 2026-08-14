# 管理控制台设计:apikey 管理 + 报表 + 额度

日期:2026-08-14
状态:已批准(待实现)
分支:main(本地开发先行,不部署)

## 1. 背景与目标

- 现状:apikey 管理接口(sentiment 8 个)已存在,但 **agent 写死 sentiment、role 创建时写死 normal、无创建后改角色、无历史报表**;无管理前端页面。
- 目标:单文件前端管理控制台(`web/admin.html`,三 tab:apikey 管理/报表/额度),跨 agent 操作,复用现有计费公共组件,后端只做最小扩展。
- 约束:**尽量减少改动范围**。不改现有 sentiment api.py、auth.py、数据模型、docker;新接口走新增共享模块 `common/admin_api.py`。

## 2. 改动范围

| 文件 | 改动 |
|---|---|
| `common/auth.py` | 新 `is_super_admin(token)`(token == ADMIN_APIKEY env,超管判定) |
| `common/apikey_mgmt.py` | 扩 `create_apikey` 加可选额度参数;`deactivate_apikey` 改 1 行(超管放行);新 `set_role`;新 `list_keys`;新 `list_agents` |
| `common/billing.py` | 新 `report_summary`;新 `report_history` |
| `common/admin_api.py` | **新** FastAPI app,前缀 `/api/v1/admin/`,挂载 `web/admin.html` |
| `web/admin.html` | **新** 单文件三 tab(原生 JS,无构建/无 CDN) |
| `tests/test_admin_api.py` | **新** pytest,SQLite 后端 |

**不改**:`agents/*/api.py`、`common/db.py`、现有 billing 函数、数据模型(agent_api_keys / agent_billing_records 已统一)、docker。

**复用现有组件**(零改动):`update_apikey`、`ensure_admin`(apikey_mgmt)、`add_free_quota/add_paid_quota/usage_all`(billing)、`db.py` 双后端。

**微改**(1 行):`deactivate_apikey` 内部鉴权改为"非超管才 require_admin"(见 §5)。

## 3. 数据模型

沿用现有,无改动:

- `agent_api_keys`(复合主键 apikey+agent):apikey/agent/role/status/free_quota/paid_quota/free_used/paid_used/created_at/updated_at
- `agent_billing_records`:apikey/agent/bill_no/status(pending|committed|cancelled)/quota_type(free|paid)/created_at/committed_at

**关键语义**:apikey 即用户,一 key 默认一 agent(创建时绑定);同 key 绑多 agent 是例外(PK 允许),管理台按 (apikey, agent) 行维度操作,不特殊支持多 agent 共享。

## 4. 接口设计(9 个新增,薄层转调)

前缀 `/api/v1/admin/`。**所有 key 定向操作 body 传参,key 不进 URL**(防 access log 泄露凭据)。

| 方法 | 路径 | 功能 | 转调 |
|---|---|---|---|
| GET | /agents | agent 下拉列表(`SELECT DISTINCT agent`,附 active key 数) | `apikey_mgmt.list_agents`(新) |
| GET | /apikeys?agent= | 跨 agent 全量(含 admin/软删),agent 可选过滤 | `apikey_mgmt.list_keys`(新) |
| POST | /apikeys | 创建 `{agent, role, free_quota?, paid_quota?}`(缺省 free 10/paid 0) | `create_apikey`(扩额度参) |
| PATCH | /apikeys | 改角色 `{apikey, agent, role}` | `set_role`(新) |
| PUT | /apikeys | 换 key `{apikey, agent, new_apikey}` | `update_apikey`(现有) |
| DELETE | /apikeys | 软删 `{apikey, agent}` | `deactivate_apikey`(微改) |
| POST | /apikeys/quota | 增额度 `{apikey, agent, type: free\|paid, count}` | `add_free/paid_quota`(现有) |
| GET | /report/summary | 按 agent 汇总(仅 active key) | `report_summary`(新) |
| GET | /report/history | 按天 committed 扣费趋势 | `report_history`(新) |

**GET /apikeys 响应**(每行):
```json
{"apikey": "sk-...", "agent": "sentiment", "role": "admin", "status": "active",
 "free": {"total": 99999999, "used": 0, "remaining": 99999999},
 "paid": {"total": 0, "used": 0, "remaining": 0}}
```
含 admin key、软删 key(status='deleted'),供管理台完整展示。

## 5. 鉴权

- 请求头 `Authorization: Bearer <ADMIN_APIKEY>`(.env 超级管理员),不等即 403。
- **console 只对超级管理员开放**,不放大每 agent 管理员权限(防跨 agent 数据泄露)。每 agent 管理员(role=admin)仍走各 agent 现有 admin 接口,不进 console。
- `ADMIN_APIKEY` 必配,未配 console 锁死(文档/启动日志注明)。
- **超管放行机制**:`auth.is_super_admin(token)`(token == ADMIN_APIKEY env)。`deactivate_apikey` 微改 1 行 —— 内部鉴权 `if not is_super_admin(admin): require_admin(admin, agent)`;新 `set_role` 同模式。**超管不依赖 (ADMIN_APIKEY, agent) 行**,新 agent 无该行也能操作;每 agent 管理员路径不变(仍 require_admin)。不能靠 `ensure_admin` 兜底 —— 其幂等检查是"有任一 active admin 就跳过",不保证 ADMIN_APIKEY 行存在。
- `set_role` 守卫:仅"不可改自己"(防锁死);admin 目标可降级/停用,与 deactivate 哲学一致 —— `ensure_admin` 幂等,现存 admin 全软删/降级时重启可重建,无"全停死"风险。
- 换 key:`update_apikey` 现有守卫(admin key 不可换 403、软删不可换 400、冲突 409)沿用。

## 6. 报表数据结构

**summary**(GET /report/summary?agent=,仅 status='active'):
```json
{"agents": [{"agent": "sentiment", "key_count": 12, "free_used": 34, "free_remaining": 86,
             "paid_used": 5, "paid_remaining": 95}],
 "total": {"free_used": ..., "free_remaining": ..., "paid_used": ..., "paid_remaining": ...}}
```
口径:**只汇总 active key**。软删 key 额度已释放(不可再扣),used 计入会失真。

**history**(GET /report/history?agent=&days=30):
```json
{"series": [{"date": "2026-08-10", "agent": "sentiment", "committed": 12}, ...]}
```
口径:**只统计 committed**,`GROUP BY date(committed_at), agent`。cancelled 行无 committed_at(NULL)不可按天,pending 是瞬时态不算"使用"。

## 7. 前端 `web/admin.html`

单文件原生 JS,fetch 调 `/api/v1/admin/*`,顶部管理员登录框(Bearer),沿用 demo.html 风格。三 tab:

1. **apikey 管理**:全量表格(agent/apikey/role/status/额度已用/剩余)+ 创建弹窗(选 agent+role+初始额度)+ 行操作(换 key/改角色/软删/增额度)。
2. **报表**:summary 卡片(每 agent 用量)+ 按 agent 分组明细 + 历史趋势图(按天 committed,手写 canvas 柱状图,无 CDN)+ 过滤(agent/天数)。
3. **额度管理**:批量增额度(选多 key 一次加)、低额度预警(remaining < N 高亮)、额度健康度总览。

## 8. 错误处理

- `ADMIN_APIKEY` 未配或 Bearer 不符 → 403
- 创建额度参数非法(< 0 或非整数)→ 400
- 换 key:格式非法 400 / 原 key 不存在 404 / admin key 403 / 软删 400 / 新 key 冲突 409(沿用 update_apikey)
- 软删:目标不存在 404 / 不可停用自己 403(沿用 deactivate_apikey)
- history 天数超上限 → 400(上限 365)

## 9. 安全

- **apikey 不进 URL**:所有 key 定向操作 body 传参,防 access log(path/query)泄露凭据。
- GET list 返回完整 key 是管理台必需;配合**无 CDN/无外链/无 eval**,降低 XSS 面。
- 结构化日志(service=admin_console component=admin_api),apikey 脱敏 `_mask_apikey` 复用于日志(OBS-CORE-003)。

## 10. 测试

- `pytest tests/test_admin_api.py`(SQLite 后端):创建(带 agent/role/额度、负额度 400)、改角色(自己 403)、换 key、软删、增额度、summary(仅 active)、history(按天 committed)、鉴权(非 ADMIN_APIKEY 403)。
- 前端:起 `uvicorn common.admin_api:app` 浏览器实测三 tab 全流程(webapp-testing)。

## 11. 实施步骤

1. `auth.py`:新 `is_super_admin(token)`;`apikey_mgmt.py` 扩展:`deactivate_apikey` 微改 1 行(超管放行)、`create_apikey` 额度参数 + 校验、`set_role`、`list_keys`/`list_agents`(复用 admin_list 行结构)
2. `billing.py`:`report_summary`(仅 active)、`report_history`(committed 按天)
3. `common/admin_api.py`:FastAPI app + 鉴权 + 9 接口 + 挂载 admin.html
4. `web/admin.html`:三 tab
5. `tests/test_admin_api.py`
6. 测试通过后 commit + CHANGELOG(common 改动 → 根项目级区)

## 12. 范围外

- 部署生产(docker compose、nginx)—— 本地先行,另行确认
- 每 agent 管理员登录 console(防权限放大,仅超级管理员)
- 软删恢复 reactivate(无此组件,重建即可)
- 历史 canceled/pending 按天趋势、quota 变动流水
- 同 key 多 agent 的批量操作

## 13. CHANGELOG

common/auth.py、common/apikey_mgmt.py、common/billing.py、common/admin_api.py、web/admin.html 均属项目级 → 根 CHANGELOG.md 项目级区。
