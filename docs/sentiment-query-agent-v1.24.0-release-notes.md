# 海外舆情检索方案生成 Agent — v1.24.0 更新说明(供开发人员同步)

版本:v1.24.0
发布日期:2026-08-12
生产地址:`http://10.33.17.72`(API 端口 `8000`,演示页端口 `80`)
完整接口文档:[agents/sentiment_query_agent/API.md](../agents/sentiment_query_agent/API.md)

> 本版本核心变化:**从"单一 apikey 的单用户计费"升级为"多用户配额与资费管理"**。每个 apikey 即一个用户,拥有独立的免费/付费额度,由管理员统一管理。存储从 JSON 文件迁移到 MySQL。

---

## 一、本次版本做了什么总览

| 维度 | 变化 |
|---|---|
| 用户模型 | apikey 即用户(此前 apikey→用户映射,一个 key 一个用户) |
| 配额 | 每个 apikey 免费额度(初始 10)+ 付费额度(管理员充值) |
| 计费 | commit 扣 1 次,先扣免费、免费用完扣付费 |
| 管理员 | 新增管理员角色,可创建/删除/修改 apikey、查全部额度、调额度 |
| 存储 | 计费 JSON 文件 → MySQL(agentstore 库,api_keys + billing_records 两表) |
| 新增接口 | 8 个(apikey 管理 5 个 + 资费查询 3 个) |
| 鉴权 | `API_KEYS_JSON` 环境变量废弃,apikey 存 MySQL |

---

## 二、新增接口(8 个)

### 2.1 apikey 管理(仅管理员,5 个)

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/v1/apikeys` | POST | 创建 apikey(默认免费 10 / 付费 0) |
| `/api/v1/apikeys` | PUT | 修改 apikey(旧 key→新 key,资费/历史数据全部迁移) |
| `/api/v1/apikeys/{apikey}` | DELETE | 删除 apikey(软删,数据保留但不可访问) |
| `/api/v1/apikeys/list` | GET | 查所有普通用户额度(按 apikey 分类) |
| `/api/v1/apikeys/pending` | GET | 查当前 apikey 的 pending(未入库)任务 |

### 2.2 资费查询(3 个)

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/v1/billing/usage` | GET | 查额度:普通用户查自己,管理员查全部 |
| `/api/v1/billing/quota/paid` | POST | 增加付费额度(仅管理员) |
| `/api/v1/billing/quota/free` | POST | 增加免费额度(仅管理员) |

---

## 三、涉及变更的既有接口(2 个)

| 接口 | 变更内容 |
|---|---|
| `POST /api/v1/groups`(提交任务) | 新增额度校验:免费+付费剩余额度 ≤ 0 时返回 **403「额度不足,请联系管理员充值」** |
| 所有 `/api/v1/*` | 鉴权从 `API_KEYS_JSON` 改为 MySQL 查 apikey;无效/已删除 apikey 返回 401 |

---

## 四、管理员说明

- **管理员 apikey**:`sk-demo-hefangyuan20260810`(额度 99999999,不受权限限制)
- 管理员可:创建/修改/删除普通用户 apikey、查全部用户额度、给任意用户增加免费/付费额度
- 普通用户只能查自己的额度(`/api/v1/billing/usage`),调管理接口返回 **403「仅管理员可操作」**

### 给开发人员/运营的使用流程

1. 管理员创建用户 apikey:`POST /api/v1/apikeys {"apikey": "sk-xxx"}`
2. (可选)给用户充值:`POST /api/v1/billing/quota/free {"apikey": "sk-xxx", "count": 数}`
3. 用户拿 apikey 调原有的提交/进度/勾选/入库/导出接口,流程不变
4. 用户额度用尽会 403,需管理员充值;`GET /api/v1/billing/usage` 可查剩余额度

---

## 五、配额与计费规则

| 规则 | 说明 |
|---|---|
| 额度构成 | 免费额度(初始 10)+ 付费额度(管理员充值,初始 0) |
| 扣减时机 | commit(确认入库)时扣 1 次,先扣免费、免费用完扣付费 |
| 不计费 | 未 commit 的任务(失败/停止/放弃)不扣额度 |
| 并发限制 | 每用户最多 5 个 pending 任务,超限返回 429 |
| 提交校验 | 提交时校验剩余额度 > 0,不足返回 403 |
| 额度查询 | `GET /api/v1/billing/usage` 返回 free/paid 的 total/used/remaining + pending 数 |

---

## 六、不影响开发人员的部分

- 原有 6 步流水线(实体测绘→…→频次定级)逻辑不变
- 原有 9 个接口(提交/进度/status/方案/勾选/stop/入库/导出/health)的请求/响应格式**不变**
- 方案组状态机(review/committed/stopped)不变
- 前端演示页无需改动(鉴权头不变,Bearer apikey)

---

## 七、需要开发人员注意的变更

1. **鉴权方式**:apikey 不再由 `.env` 的 `API_KEYS_JSON` 配置,而是管理员通过接口创建、存 MySQL。**新增 apikey 找管理员**。
2. **新增 403 错误处理**:调用方需处理「额度不足」(403)与「仅管理员可操作」(403)两种新错误。
3. **管理员接口**:普通用户调用会 403,需管理员 apikey。
4. **删除 apikey 影响**:被删除的 apikey 立即失效(401),历史数据保留但不可访问。
5. **修改 apikey 影响**:旧 key 立即失效,新 key 继承全部额度与历史数据,需同步更新调用方。

---

## 八、错误码新增/变更

| HTTP | detail | 新增/变更 |
|---|---|---|
| 403 | 额度不足,请联系管理员充值 | 新增 |
| 403 | 仅管理员可操作 | 新增 |
| 401 | apikey 无效或已删除 | 变更(原"apikey 无效") |
| 400 | apikey 格式:sk- 开头 + 6-64 位字母数字 | 新增 |
| 409 | apikey 已存在 | 新增 |
| 403 | 不可删除管理员 apikey | 新增 |