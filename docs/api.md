# Agent1 API 接口文档

海外舆情检索方案生成 Agent 的 HTTP 接口。基于 FastAPI,所有接口返回 JSON。

- **Base URL**: `http://<host>:8000`
- **认证**: 所有 `/api/v1/*` 接口需 `Authorization: Bearer <apikey>` 头
  - apikey 在服务端 `.env` 的 `API_KEYS_JSON` 配置(`{"<apikey>": "<用户标识>"}`)
  - 无效/缺失 apikey → 401
  - 跨用户访问资源 → 403

## 接口一览

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/health` | 健康检查(免认证) |
| POST | `/api/v1/groups` | 提交任务:创建方案组,后台跑 6 步流水线 |
| GET | `/api/v1/groups/{id}/progress` | 查 6 步进度(每步状态/产物) |
| GET | `/api/v1/groups/{id}/schemes` | 获取方案组(方案/轨/检索式/勾选态) |
| PUT | `/api/v1/groups/{id}/selection` | 提交勾选(方案级 + 轨级) |
| POST | `/api/v1/groups/{id}/commit` | 确认入库(计费点) |
| GET | `/api/v1/groups/{id}/export` | 导出 Excel(勾选轨 → 三 sheet) |

**状态流转**: `generating`(生成中)→ `review`(待勾选)→ `committed`(已入库,冻结);失败为 `failed`。

---

## 1. 健康检查

```
GET /health
```

**响应**:

```json
{"status": "ok"}
```

## 2. 提交任务(创建方案组)

```
POST /api/v1/groups
Authorization: Bearer <apikey>
Content-Type: application/json
```

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `company_name` | string | ✅ | 中文公司名(一个待搜索主体) |
| `role` | string | — | 主体角色:`承包商` / `业主` / `ai判定`(默认) |
| `regions` | string[] | — | 重点地区,空 = AI 推断 |
| `query_types` | string[] | — | 检索类型:全量新闻/负面新闻/行业新闻/招标/快讯/司法,空 = 全部 |

**示例**:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/groups \
  -H "Authorization: Bearer sk-demo-key" \
  -H "Content-Type: application/json" \
  -d '{"company_name": "中国十五冶金建设集团有限公司"}'
```

**响应 200**:

```json
{"group_id": "3a9bf94a548341f5", "status": "generating"}
```

| 状态码 | 场景 |
|---|---|
| 400 | `company_name` 为空 |
| 401 | apikey 无效/缺失 |
| 429 | 该用户未入库方案组超过 5 个(并发 pending 超限) |

**说明**: 提交后立即返回 `group_id`,6 步流水线在后台异步执行(约 6 分钟)。每用户最多 5 个未入库方案组。

## 3. 查 6 步进度

```
GET /api/v1/groups/{group_id}/progress
Authorization: Bearer <apikey>
```

**响应 200**(生成中):

```json
{
  "group_id": "3a9bf94a548341f5",
  "status": "generating",
  "step_status": [
    {"step": 1, "status": "done", "output": {"entities": {...}}},
    {"step": 2, "status": "running", "output": null},
    {"step": 3, "status": "pending", "output": null}
  ]
}
```

| 字段 | 说明 |
|---|---|
| `status` | `generating` / `review` / `committed` / `failed` |
| `step_status[].step` | 1-6:实体测绘/主体画像/关键词字典/双轨检索式/属地信源/频次定级 |
| `step_status[].status` | `pending` / `running` / `done` / `error` |
| `step_status[].output` | 该步产物(JSON),done 后才有 |
| `step_status[].error` | 失败原因,error 时才有 |

**说明**: 生成中从 checkpoint 实时读,完成后读文件。生成中 404 已修复,不会出现。

## 4. 获取方案组

```
GET /api/v1/groups/{group_id}/schemes
Authorization: Bearer <apikey>
```

**响应 200**:

```json
{
  "group_id": "3a9bf94a548341f5",
  "company_name": "中国十五冶金建设集团有限公司",
  "meta": {"role": "ai判定", "regions": [], "query_types": []},
  "status": "review",
  "schemes": [
    {
      "id": "Q0",
      "name": "集团层",
      "region": "全语种",
      "lang": "中/英",
      "desc": "...",
      "gaps": ["GAP001 俄语关键词未覆盖"],
      "selected": true,
      "tracks": [
        {
          "key": "a",
          "boolean_query": "(...)",
          "google_query": "(...)",
          "sources": ["属地媒体.com"],
          "frequency": "周级",
          "relevance": "direct",
          "selected": true
        }
      ]
    }
  ],
  "keywords": [
    {"layer": "A", "category": "A1集团/公司名称簇", "terms": "...", "lang": "全", "guard": "", "note": ""}
  ]
}
```

| 字段 | 说明 |
|---|---|
| `schemes[]` | 方案列表(6-8 个),每个含多轨 |
| `schemes[].tracks[].key` | 轨类型:`全量新闻` / `负面新闻` / `行业新闻` / `快讯` / `司法` / `招标` |
| `schemes[].tracks[].frequency` | `快讯/小时级` / `日级` / `周级` / `双周级` / `月级` |
| `schemes[].tracks[].relevance` | `direct` / `indirect` / `context` |
| `keywords[]` | 关键词字典(六层 A/B/C/D/R/X) |

## 5. 提交勾选

```
PUT /api/v1/groups/{group_id}/selection
Authorization: Bearer <apikey>
Content-Type: application/json
```

**请求体**(勾选保留的方案与轨;未出现的方案/轨保持原值):

```json
{
  "schemes": {"Q0": true, "Q1": false},
  "tracks": {"Q0": {"全量新闻": true, "负面新闻": true, "行业新闻": false, "快讯": false, "司法": false, "招标": false}}
}
```

**响应 200**:

```json
{"group_id": "3a9bf94a548341f5", "updated": true}
```

| 状态码 | 场景 |
|---|---|
| 404 | 方案组不存在 |
| 403 | 非本人方案组 |
| 409 | 已入库(冻结),不可改勾选 |

## 6. 确认入库(计费点)

```
POST /api/v1/groups/{group_id}/commit
Authorization: Bearer <apikey>
```

**响应 200**:

```json
{"group_id": "3a9bf94a548341f5", "status": "committed"}
```

**说明**:
- 固化正式文件(草稿删除),方案组冻结,计费记录 pending → committed(1 单位)
- 409:已入库

## 7. 导出 Excel

```
GET /api/v1/groups/{group_id}/export
Authorization: Bearer <apikey>
```

**响应 200**: Excel 文件(`{group_id}_tasks.xlsx`,3 sheet:检索任务清单 / 关键词字典 / 调度说明)

- 只导出勾选的轨(勾选轨数 = 任务行数)
- 检索任务清单列:任务ID | 检索组 | 国家/地区 | 语种 | 检索式(布尔) | 检索式(Google) | 目标信源白名单 | 建议频次 | 命中期望相关度 | 状态 | 运营注/说明

---

## 错误响应格式

```json
{"detail": "错误信息"}
```

| 状态码 | 含义 |
|---|---|
| 400 | 请求参数错误 |
| 401 | apikey 无效/缺失 |
| 403 | 越权访问他人方案组 |
| 404 | 方案组/计费记录不存在 |
| 409 | 状态冲突(已入库/冻结) |
| 429 | 并发 pending 超限 |

## 前端联调

前端演示页 `web/demo.html` 已封装全部接口(轮询 progress + 勾选 + 入库 + 导出)。API 地址自动取当前访问的 host:`window.location.protocol + "//" + window.location.hostname + ":8000"`。
