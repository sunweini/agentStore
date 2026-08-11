# 海外舆情检索方案生成 Agent — 接口文档

版本:v1.24.0(2026-08-11)
生产地址:`http://10.33.17.72`(API 端口 `8000`,演示页端口 `80`)

## 0. 版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
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

apikey 由服务方分配(`.env` 的 `API_KEYS_JSON` 配置 apikey→用户映射)。用户标识决定**资源归属**(只能访问自己创建的方案组)与**计费**。

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

## 3. 错误码汇总

| HTTP | detail | 场景 |
|---|---|---|
| 400 | company_name 必填 | 提交时公司名为空 |
| 401 | 缺少 Authorization: Bearer <apikey> | 未带鉴权头 |
| 401 | apikey 无效 | apikey 未在服务端注册 |
| 403 | 无权访问该方案组 | 访问其他用户的方案组 |
| 404 | 方案组不存在 | group_id 错误 |
| 409 | 方案组已入库冻结,不可改勾选 | commit 后调 selection |
| 409 | 已入库 | 重复 commit |
| 429 | 并发 pending 超限,请先完成或取消未入库的方案组 | 同用户未完成方案组 > 5 |

## 4. 计费规则

- 提交任务 = 记 1 条 `pending` 记录;`commit` = 转正式计费(1 单位)
- 未 commit 的方案组(失败/放弃)不计费;pending 超 24 小时自动视为放弃
- 同用户最多 5 个 pending(防刷)

## 5. 性能参考(生产实测,2026-08-10)

- 全流程 6 步:约 5-15 分钟(取决于 LLM 响应与搜索耗时)
- 轮询建议间隔:10-15 秒
- 单步失败不中断,最终仍可取部分产物
