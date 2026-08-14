# 海外舆情检索方案生成 Agent — 接口文档

版本:v1.26.0(2026-08-14)
生产地址:`http://10.33.17.72`(API 端口 `8000`,演示页端口 `80`)

## 0. 版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.26.0 | 2026-08-14 | **全局账单接口** `GET /api/v1/billing/usage_all`(管理员跨 agent 看全部账单,`?agent=` 可选过滤);现有 usage / apikeys/list 响应新增 `agent` 字段(additive);生产切换前置:migrate_billing.py 迁存量后再部署 |
| v1.25.0 | 2026-08-14 | **计费/鉴权/apikey 管理切公共组件**(common.billing/auth/apikey_mgmt,agent='sentiment',统一表 agent_api_keys/agent_billing_records);失败路径补 cancel_pending;行为变化(admin 可停用 / update 去 owner 迁移)。接口端点/参数/返回不变 |
| v1.24.0 | 2026-08-11 | **多用户配额与资费**:apikey 即用户,免费/付费额度,apikey 管理(创建/修改/删除),管理员,8 新接口,MySQL 存储(未部署,feature 分支) |
| v1.2.0 | 2026-08-11 | 生产三错修复(bad_json/token超限/terms缺失);max_tokens=32768;去掉 thinking disabled;step6 risk 字段修复 |
| v1.1.0 | 2026-08-07 | load_skill 方法论接入(每步 LLM 可调方法论,2 回合上限) |
| v0.1.0 | 2026-08-10 | 生产部署,9 接口全链路验证 |

## 1. 概述

输入一个中文公司名,Agent 自动跑 6 步流水线(实体测绘→主体画像→关键词字典→双轨检索式→属地信源→频次定级),产出**方案组**(含多个方案,每方案含多条检索轨),经调用方勾选确认后入库,可导出 Excel 检索任务清单。

### 调用流程

```
POST /api/v1/groups                提交任务,得 group_id
  ↓ 轮询(建议 10-15s 间隔,全流程约 5-15 分钟)
GET  /api/v1/groups/{id}/progress  查 6 步进度,status=review 表示可勾选
GET  /api/v1/groups/{id}/status    轻量心跳:只回 status + running(可选,比 progress 便宜)
POST /api/v1/groups/{id}/stop      停止生成中的任务(可选,不需要重启服务)
  ↓
GET  /api/v1/groups/{id}/schemes   取方案组(方案/轨/检索式)
  ↓
PUT  /api/v1/groups/{id}/selection 提交勾选(方案级 + 轨级)
  ↓
POST /api/v1/groups/{id}/commit    确认入库(冻结,计费 1 单位)
  ↓
GET  /api/v1/groups/{id}/export    导出 Excel(勾选的轨 → 任务行)
```

### 鉴权

所有 `/api/v1/*` 接口需请求头:

```
Authorization: Bearer <apikey>
```

**v1.24.0 起**:apikey 即用户,存 MySQL(api_keys 表),由管理员通过 `POST /api/v1/apikeys` 创建(默认免费额度 10 次)。apikey 决定**资源归属**(只能访问自己创建的方案组)与**额度**(commit 扣减)。管理员 apikey(`ADMIN_APIKEY`)额度 99999999,不受归属限制。

**v1.25.0 起**:apikey 存统一表 `agent_api_keys`(agent='sentiment',与 contract 同表同 schema,(apikey, agent) 复合主键,额度按 agent 维度隔离),计费记录存 `agent_billing_records`(bill_no=group_id);计费/鉴权/apikey 管理统一走公共组件 `common.billing` / `common.auth` / `common.apikey_mgmt`。**接口端点/参数/返回不变**,仅存量存储表切换。

> ⚠️ **生产切换前置(重要)**:存量 apikey 在老表 `api_keys` / `billing_records`。**部署 v1.25.0+ 前必须先迁移**:`python3 scripts/migrate_billing.py --dry-run`(验证)→ `--apply`(实迁,幂等 + 迁移后校验),把存量迁入 `agent_*` 表后再部署 —— **否则现有 apikey 全部 401**。老表保留不删(回滚路径)。

**v1.24.0 前**:apikey 由 `.env` 的 `API_KEYS_JSON` 配置 apikey→用户映射(已废弃)。

### 通用约定

- Content-Type:`application/json; charset=utf-8`(export 除外)
- 错误响应统一结构:`{"detail": "<中文错误描述>"}`
- 方案组状态机:`generating`(生成中)→ `review`(待勾选)→ `committed`(已入库,冻结)

## 2. 接口详解

### 2.0 GET /health — 健康检查

无需鉴权。

**响应 200**(真实返回):
```json
{"status":"ok"}
```

---

### 2.1 POST /api/v1/groups — 提交任务

创建方案组,后台异步跑 6 步流水线。立即返回,不阻塞。

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `company_name` | string | 是 | 中文公司名(如"中国中铁") |
| `role` | string | 否 | 主体角色:`承包商`/`业主`/`ai判定`(默认 `ai判定`,由 AI 推断) |
| `regions` | string[] | 否 | 重点地区(如 `["赞比亚","波兰"]`);空 = AI 推断 |
| `query_types` | string[] | 否 | 检索类型子集:`全量新闻`/`负面新闻`/`行业新闻`/`招标`/`快讯`/`司法`;空 = 全部 |

**请求示例**:
```bash
curl -X POST http://10.33.17.72:8000/api/v1/groups \
  -H "Authorization: Bearer <apikey>" \
  -H "Content-Type: application/json" \
  -d '{"company_name":"中国中铁","role":"承包商"}'
```

**响应 200**(真实返回):
```json
{"group_id":"4c777cadc3844a94","status":"generating"}
```

**错误**:
- `400 {"detail":"company_name 必填"}`
- `429 {"detail":"并发 pending 超限,请先完成或取消未入库的方案组"}`(同用户最多 5 个未完成方案组)

---

### 2.2 GET /api/v1/groups/{group_id}/progress — 查询进度

轮询用。生成中返回实时步骤进度;完成后返回最终状态。

**响应 200 — 生成中**(真实返回,step 1 完成、后续进行中):
```json
{
  "group_id": "b83d8ca8b53c46c3",
  "status": "generating",
  "step_status": [
    {
      "step": 1,
      "status": "done",
      "output": {
        "entities": {
          "parent": "中国铁路工程集团有限公司",
          "subsidiaries": ["中国中铁股份有限公司", "中铁一局集团有限公司", "..."],
          "overseas_entities": [
            {"name": "China Railway Group Limited", "lang": "en", "region": "中国香港(上市主体)"},
            {"name": "China Overseas Engineering Group Co., Ltd. (COVEC)", "lang": "en", "region": "非洲/欧洲/东南亚"}
          ],
          "spelling_variants": ["中国中铁", "CREC", "China Railway Group", "..."],
          "interference_sources": ["中国铁建(China Railway Construction Corporation, CRCC)"]
        }
      }
    }
  ]
}
```

**响应 200 — 完成**(真实返回,output 内容省略):
```json
{
  "group_id": "4c777cadc3844a94",
  "status": "review",
  "step_status": [
    {"step": 1, "status": "done", "output": {"entities": {"...": "实体测绘产物"}}},
    {"step": 2, "status": "done", "output": {"profile": {"...": "主体画像产物"}}},
    {"step": 3, "status": "done", "output": {"keywords": ["...关键词字典产物"]}},
    {"step": 4, "status": "done", "output": {"schemes": ["...检索式产物"]}},
    {"step": 5, "status": "done", "output": {"schemes": ["...信源产物"]}},
    {"step": 6, "status": "done", "output": {"schemes": ["...频次定级产物"]}}
  ]
}
```

**step_status 单项字段**:

| 字段 | 说明 |
|---|---|
| `step` | 步骤号 1-6 |
| `status` | `running` 执行中 / `done` 完成 / `error` 失败 |
| `output` | 该步产物(done 时);error 时为 null 且有 `error` 字段 |

**单步失败不中断流水线**:某步 error 后续步骤继续跑,最终 status 仍为 `review`,但依赖该步的字段会缺失(GAP 标注)。

**错误**:`404 {"detail":"方案组不存在"}`、`403 {"detail":"无权访问该方案组"}`

---

### 2.3 GET /api/v1/groups/{group_id}/schemes — 获取方案组

返回完整方案组:方案列表 + 关键词字典 + 勾选状态。建议 `status=review` 后调用。

**响应 200**(真实返回,检索式内容节选):
```json
{
  "group_id": "4c777cadc3844a94",
  "company_name": "中国中铁",
  "meta": {"role": "承包商", "regions": [], "query_types": []},
  "status": "review",
  "schemes": [
    {
      "id": "Q0",
      "name": "集团层",
      "region": "全语种",
      "lang": "中/英",
      "desc": "集团整体层面舆情监测",
      "gaps": [],
      "selected": false,
      "tracks": [
        {
          "key": "全量新闻",
          "boolean_query": "(\"中国中铁股份有限公司\" OR \"中国中铁\" OR \"China Railway Group Limited\" OR (CREC AND (\"China\" OR \"railway\" OR \"中铁\")) OR \"中铁股份\" OR \"中铁一局\" OR ...)",
          "google_query": "(\"中国中铁股份有限公司\" OR \"中国中铁\" OR \"China Railway Group Limited\" OR (CREC (\"China\" OR \"railway\" OR \"中铁\")) OR ...)",
          "sources": ["people.com.cn", "xinhuanet.com", "chinadaily.com.cn", "gov.cn", "reuters.com", "bbc.com", "apnews.com"],
          "frequency": "周级",
          "relevance": "direct",
          "selected": false
        },
        {"key": "负面新闻", "...": "..."}
      ]
    },
    {"id": "Q1", "name": "东南亚项目群", "region": "泰国/新加坡/马来西亚", "...": "..."},
    {"id": "Q2", "name": "波兰项目群", "region": "波兰", "...": "..."},
    {"id": "Q3", "name": "非洲项目群", "region": "赞比亚/南非", "...": "..."}
  ],
  "keywords": [
    {
      "layer": "A",
      "category": "A1集团/公司名称簇",
      "terms": "\"中国中铁股份有限公司\" \"中国中铁\" \"China Railway Group Limited\"",
      "lang": "全",
      "guard": "",
      "note": "法定全称与官方英文名;中文检索不加宽泛地域词,避免本国招采信息淹没"
    },
    {"layer": "B", "...": "...共 15 条,A/B/C/D/R/X 六层"}
  ]
}
```

**字段说明**:

| 字段 | 说明 |
|---|---|
| `schemes[].id` | 方案编号 Q0/Q1/Q2…(Q0 通常是集团层) |
| `schemes[].tracks[].key` | 轨类型,固定 6 值:`全量新闻`/`负面新闻`/`行业新闻`/`快讯`/`司法`/`招标` |
| `schemes[].tracks[].boolean_query` | 布尔语法检索式(喂传统检索系统) |
| `schemes[].tracks[].google_query` | Google 语法检索式(喂 Google/爬虫) |
| `schemes[].tracks[].sources` | 属地信源白名单(域名列表) |
| `schemes[].tracks[].frequency` | 监测频次:`快讯`/`小时级`/`日级`/`周级`/`双周`/`月级` |
| `schemes[].tracks[].relevance` | 相关度:`direct`/`indirect`/`context` |
| `schemes[].selected` / `tracks[].selected` | 勾选态(被 selection 接口更新) |
| `keywords[].layer` | 关键词层级:A 名称簇/B 业务/C 项目/D 地域/R 风险/X 排除 |

**错误**:`404`、`403`

---

### 2.4 GET /api/v1/groups/{group_id}/status — 轻量运行状态

轮询心跳用。返回状态、是否在后台运行、当前步骤与**能否查方案组的明确标识**,不带步骤产物(比 progress 便宜)。

**"6 步跑完了吗"的判断标准**:`schemes_ready=true`(等价于 `status ∈ {review, committed}`)。status 从 `generating` 变为 `review` 只会发生在 6 步流程全部执行结束后,是唯一的完成信号。

**响应 200**(真实返回):
```json
{"group_id":"4c777cadc3844a94","status":"committed","running":false,"current_step":6,"total_steps":6,"steps_done":6,"steps_error":0,"schemes_ready":true}
```

生成中示例(跑到第 3 步):
```json
{"group_id":"7ad16e2f78584402","status":"generating","running":true,"current_step":3,"total_steps":6,"steps_done":2,"steps_error":0,"schemes_ready":false}
```

| 字段 | 说明 |
|---|---|
| `status` | `generating`/`review`/`committed`/`stopped` |
| `running` | 后台任务是否正在本进程运行 |
| `current_step` | 当前执行中的步号;无 running 步 = 已出现的最大步号;0 = 尚未开始 |
| `total_steps` | 固定 6 |
| `steps_done` | 已完成(done)的步骤数 |
| `steps_error` | 失败(error)的步骤数(单步失败不中断流程) |
| `schemes_ready` | **true = 可调 schemes 接口**;review/committed 为 true,generating/stopped 为 false |

**错误**:`404 {"detail":"方案组不存在"}`、`403 {"detail":"无权访问该方案组"}`

---

### 2.5 POST /api/v1/groups/{group_id}/stop — 停止生成中的任务

直接取消后台任务(不重启服务)。已完成步骤的产物保留,组标记 `stopped`。stopped 组不可入库(未计费)。

**请求**:无请求体。

```bash
curl -X POST http://10.33.17.72:8000/api/v1/groups/<group_id>/stop \
  -H "Authorization: Bearer <apikey>"
```

**响应 200**(真实返回):
```json
{"group_id":"4c777cadc3844a94","status":"stopped"}
```

**错误**:
- `404`、`403`
- `409 {"detail":"组状态 review,仅 generating 可停止"}`(非生成中状态)

---

### 2.6 PUT /api/v1/groups/{group_id}/selection — 提交勾选

更新方案级 + 轨级勾选状态。可多次调用(覆盖式)。已 commit 的组拒绝修改。

**请求体**:

| 字段 | 类型 | 说明 |
|---|---|---|
| `schemes` | object | `{方案id: bool}`,如 `{"Q0": true, "Q1": false}` |
| `tracks` | object | `{方案id: {轨key: bool}}`,如 `{"Q0": {"全量新闻": true, "负面新闻": false}}` |

只提交需要变更的键;未出现的键保持原值。

**请求示例**(全勾选):
```bash
curl -X PUT http://10.33.17.72:8000/api/v1/groups/4c777cadc3844a94/selection \
  -H "Authorization: Bearer <apikey>" \
  -H "Content-Type: application/json" \
  -d '{
    "schemes": {"Q0": true, "Q1": true, "Q2": true, "Q3": true},
    "tracks": {
      "Q0": {"全量新闻": true, "负面新闻": true},
      "Q1": {"全量新闻": true, "负面新闻": true, "行业新闻": true},
      "Q2": {"全量新闻": true, "负面新闻": true, "行业新闻": true},
      "Q3": {"全量新闻": true, "负面新闻": true, "行业新闻": true}
    }
  }'
```

**响应 200**(真实返回):
```json
{"group_id":"4c777cadc3844a94","updated":true}
```

**错误**:`404`、`403`、`409 {"detail":"方案组已入库冻结,不可改勾选"}`

---

### 2.7 POST /api/v1/groups/{group_id}/commit — 确认入库

固化方案组(冻结,不可再改勾选)+ 计费转正式(1 单位)。幂等校验:重复 commit 报 409。

**请求**:无请求体。

```bash
curl -X POST http://10.33.17.72:8000/api/v1/groups/4c777cadc3844a94/commit \
  -H "Authorization: Bearer <apikey>"
```

**响应 200**(真实返回):
```json
{"group_id":"4c777cadc3844a94","status":"committed"}
```

**错误**:`404`、`403`、`409 {"detail":"已入库"}`

---

### 2.8 GET /api/v1/groups/{group_id}/export — 导出 Excel

把勾选的轨转成检索任务清单 Excel(每个勾选轨 = 一行任务)。建议 commit 后调用(未 commit 也可导出当前勾选态)。

**响应 200**:二进制 `.xlsx` 文件(真实验证:13,524 字节)。

- `Content-Type: application/octet-stream`
- `Content-Disposition: attachment; filename="<group_id>_tasks.xlsx"`

```bash
curl -OJ http://10.33.17.72:8000/api/v1/groups/4c777cadc3844a94/export \
  -H "Authorization: Bearer <apikey>"
```

**错误**:`404`、`403`

---

### 2.9 POST /api/v1/apikeys — 创建 apikey(v1.24.0,仅管理员)

创建后默认免费额度 10 次、付费额度 0 次。

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `apikey` | string | 是 | 新 apikey,格式 `sk-` 开头 + 6-64 位字母数字 |

**请求示例**:
```bash
curl -X POST http://10.33.17.72:8000/api/v1/apikeys \
  -H "Authorization: Bearer <管理员apikey>" \
  -H "Content-Type: application/json" \
  -d '{"apikey": "sk-newuser001"}'
```

**响应 200**:
```json
{"apikey": "sk-newuser001", "free_quota": 10, "paid_quota": 0}
```

**错误**:`400`(格式错)、`409`(已存在)、`403`(非管理员)

---

### 2.10 PUT /api/v1/apikeys — 修改 apikey(v1.24.0,仅管理员)

旧 key → 新 key,资费继承(免费/付费额度、已用量、计费记录全部迁移到新 key;方案组文件 owner 不迁移,见 v1.25.0 行为变化)。

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `old_apikey` | string | 是 | 原 apikey |
| `new_apikey` | string | 是 | 新 apikey(格式同创建,`sk-` 开头 + 6-64 位字母数字,非法 400) |

**响应 200**:
```json
{"old_apikey": "sk-olduser001", "new_apikey": "sk-newuser002", "migrated": true}
```

**错误**:`404`(原 key 不存在)、`409`(新 key 已存在)、`403`(非管理员/原 key 是管理员)、`400`(apikey 格式错/原 key 已删除)

---

### 2.11 DELETE /api/v1/apikeys/{apikey} — 删除 apikey(v1.24.0,仅管理员)

软删除:该 apikey 立即无法调用任何接口;历史数据保留但不可访问(不迁移、不清理)。

**响应 200**:
```json
{"apikey": "sk-newuser001", "deleted": true}
```

**错误**:`404`(不存在)、`403`(非管理员)、`403`(不可停用自己 —— 目标为自身凭据;admin 目标可停用)

---

### 2.12 GET /api/v1/apikeys/list — 查所有普通用户额度(v1.24.0,仅管理员)

**响应 200**:
```json
{
  "users": [
    {"apikey": "sk-a", "agent": "sentiment", "free": {"total": 10, "used": 3, "remaining": 7},
     "paid": {"total": 5, "used": 1, "remaining": 4}},
    {"apikey": "sk-b", "agent": "sentiment", "free": {"total": 10, "used": 0, "remaining": 10},
     "paid": {"total": 0, "used": 0, "remaining": 0}}
  ]
}
```

> v1.25.0 起每个用户新增 `agent` 字段(additive,恒为 `sentiment`)。

---

### 2.13 GET /api/v1/apikeys/pending — 查当前 apikey 的 pending 任务

**响应 200**:
```json
{
  "apikey": "sk-a",
  "pending": [
    {"group_id": "4c777cadc3844a94", "created_at": "2026-08-11 10:00:00"}
  ]
}
```

---

### 2.14 GET /api/v1/billing/usage — 资费查询(v1.24.0)

普通用户查自己;管理员查全部。

**普通用户响应 200**:
```json
{
  "role": "normal",
  "apikey": "sk-a",
  "agent": "sentiment",
  "free": {"total": 10, "used": 3, "remaining": 7},
  "paid": {"total": 5, "used": 1, "remaining": 4},
  "pending_count": 2
}
```

**管理员响应 200**:
```json
{
  "role": "admin",
  "users": [
    {"apikey": "sk-a", "agent": "sentiment", "free": {...}, "paid": {...}},
    {"apikey": "sk-b", "agent": "sentiment", "free": {...}, "paid": {...}}
  ]
}
```

> v1.25.0 起新增 `agent` 字段(additive):普通用户响应在顶层,管理员响应的每个用户条目内(恒为 `sentiment`)。

---

### 2.15 POST /api/v1/billing/quota/paid — 增加付费额度(v1.24.0,仅管理员)

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `apikey` | string | 是 | 目标普通用户 apikey |
| `count` | int | 是 | 增加次数(正数) |

**响应 200**:
```json
{"apikey": "sk-a", "paid_added": 5}
```

**错误**:`400`(count ≤ 0)、`403`(非管理员)

---

### 2.16 POST /api/v1/billing/quota/free — 增加免费额度(v1.24.0,仅管理员)

同 2.15,增加免费额度。

**响应 200**:
```json
{"apikey": "sk-a", "free_added": 5}
```

---

### 2.17 GET /api/v1/billing/usage_all — 全局账单(v1.26.0,仅管理员)

管理员**跨 agent** 查看所有普通用户(role='normal' 且 active)的额度账单,供管理后台对账。与
2.14 `/billing/usage` 区分:后者管理员只看当前 agent(sentiment)的普通用户;本接口缺省返回**全部 agent**。

**请求**:`GET /api/v1/billing/usage_all`,头 `Authorization: Bearer <管理员apikey>`;可选 query 参数 `agent`(如 `?agent=contract`)仅返回该 agent。

```bash
curl http://10.33.17.72:8000/api/v1/billing/usage_all \
  -H "Authorization: Bearer <管理员apikey>"
```

**响应 200**(真实结构,两个 agent 各一名用户):
```json
{
  "usage": [
    {"apikey": "sk-a", "agent": "sentiment",
     "free": {"total": 10, "used": 3, "remaining": 7},
     "paid": {"total": 5, "used": 1, "remaining": 4}},
    {"apikey": "sk-b", "agent": "contract",
     "free": {"total": 10, "used": 0, "remaining": 10},
     "paid": {"total": 0, "used": 0, "remaining": 0}}
  ]
}
```

| 字段 | 说明 |
|---|---|
| `usage[].apikey` | 普通用户 apikey |
| `usage[].agent` | agent 维度(sentiment / contract / …) |
| `usage[].free` / `paid` | 各额度 `total` / `used` / `remaining` |

**错误**:`401`(apikey 无效)、`403 {"detail":"需要管理员权限"}`(非管理员)

---

## 3. 错误码汇总

| HTTP | detail | 场景 |
|---|---|---|
| 400 | company_name 必填 | 提交时公司名为空 |
| 400 | apikey 格式:sk- 开头 + 6-64 位字母数字 | 创建/修改 apikey 格式错 |
| 400 | count 必须为正数 | 调额度时 count ≤ 0 |
| 401 | 缺少 Authorization: Bearer <apikey> | 未带鉴权头 |
| 401 | apikey 无效或已删除 | apikey 未注册/已软删 |
| 403 | 无权访问该方案组 | 访问其他用户的方案组(管理员放行) |
| 403 | 仅管理员可操作 | 非管理员调管理接口 |
| 403 | 额度不足,请联系管理员充值 | 免费+付费额度用尽 |
| 403 | 不可停用自己 | 管理员停用自己的 apikey(admin 目标可停用,仅自身不可) |
| 404 | 方案组不存在 | group_id 错误 |
| 404 | 计费记录不存在 | commit 无 pending 记录 |
| 409 | 方案组已入库冻结,不可改勾选 | commit 后调 selection |
| 409 | 已入库 | 重复 commit |
| 409 | apikey 已存在 | 创建重复 apikey |
| 429 | 并发 pending 超限,请先完成或取消未入库的方案组 | 同用户未完成方案组 > 5 |

## 4. 计费规则(v1.24.0 起)

- **额度体系**:每个 apikey 免费额度(初始 10)+ 付费额度(充值)。提交时校验剩余额度 > 0,不足 403
- **扣减时机**:`commit` 扣 1 次,先扣免费额度,免费用完扣付费额度
- **并发**:同 apikey 最多 5 个 pending;stop 释放
- **未 commit 不计费**:失败/停止/放弃的 pending 不扣额度
- **管理员**:额度 99999999,不受权限控制,可查全部/增减额度

## 5. 性能参考(生产实测,2026-08-10)

- 全流程 6 步:约 5-15 分钟(取决于 LLM 响应与搜索耗时)
- 轮询建议间隔:10-15 秒
- 单步失败不中断,最终仍可取部分产物
