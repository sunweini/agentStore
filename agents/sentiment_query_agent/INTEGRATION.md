# INTEGRATION.md — AI Agent 对接规范(机器可读)

> 本文档供开发者的 AI agent 直接阅读并实现对接。契约完整自包含,无需其他上下文。
> 服务:海外舆情检索方案生成 Agent(API v0.1.0)。

## 0. 角色与目标

你是对接方 AI agent。你的任务:调用下述 HTTP API,完成「提交公司名 → 轮询进度 → 获取方案 → 勾选 → 入库 → 导出 Excel」全流程。严格遵守本文档的字段契约与状态机,不得臆造字段或接口。

## 1. 基础契约

- Base URL:`http://10.33.17.72:8000`(生产)。路径前缀 `/api/v1`。
- 鉴权:每个 `/api/v1/*` 请求必须带 header `Authorization: Bearer <API_KEY>`。`API_KEY` 由人工提供,不要编造;未提供时向用户索要。
- 请求/响应体均为 UTF-8 JSON(`Content-Type: application/json`)。
- 错误响应结构恒为 `{"detail": "<中文描述>"}`,HTTP 状态码见 §7。
- `group_id` 是 16 位十六进制字符串(如 `4c777cadc3844a94`),由服务端生成,后续所有接口路径参数都用它。

## 2. 状态机(必须遵守)

```
提交后 status=generating
轮询 progress 直到 status ∈ {review}
取 schemes → 按业务规则勾选 → selection
commit(一次性,之后冻结)
export(可选)
```

辅助接口(可选用):
- `GET .../status`:轻量心跳,返回 status/running/current_step/steps_done/
  steps_error/schemes_ready;判断"能否查方案组"只看 `schemes_ready=true`。
- `POST .../stop`:停止 generating 中的任务(用户放弃时调用,无需重启服务);
  停止后 status=stopped,产物保留但**不可 commit**。

规则:
1. `generating` 期间**禁止**调 schemes/selection/commit(数据不全)。
2. 轮询间隔 ≥ 10 秒;**禁止**间隔 < 3 秒的忙轮询。全流程耗时 5-15 分钟,总超时预算 ≥ 25 分钟。
3. `committed` 后**禁止**再调 selection(409)。
4. 单步失败(step_status[].status="error")不中断流程;status 仍会变 review,但产物可能有缺失。遇到 error 步骤:记录并继续,向用户报告哪步失败。
5. **只有 status=review 的组可以 commit**;generating/stopped 状态 commit 会被 409 拒绝。
6. 用户明确表示放弃/停止时:调 stop,不要等流程跑完。

## 3. 接口契约

### 3.1 POST /api/v1/groups — 提交

请求体:
```json
{
  "company_name": "中国中铁",          // 必填,非空中文字符串
  "role": "ai判定",                    // 可选: 承包商|业主|ai判定,缺省 ai判定
  "regions": [],                       // 可选: 重点地区字符串数组,空=AI推断
  "query_types": []                    // 可选: 检索类型数组,空=全部
}
```
`query_types` 合法值(只能是这 6 个):`全量新闻`、`负面新闻`、`行业新闻`、`招标`、`快讯`、`司法`。

成功响应 200(真实样例):
```json
{"group_id":"4c777cadc3844a94","status":"generating"}
```
保存 `group_id`。失败:400(公司名空)、401(apikey)、429(并发超限,提示用户清理未完成方案组后重试)。

### 3.2 GET /api/v1/groups/{group_id}/progress — 轮询

响应 200:
```json
{
  "group_id": "4c777cadc3844a94",
  "status": "generating|review|committed",
  "step_status": [
    {"step": 1, "status": "running|done|error", "output": {"...": "该步产物或null"}, "error": "仅error时存在"}
  ]
}
```
- `step` 取值 1-6,顺序出现(已开始的步骤)。
- 终止条件:`status != "generating"`。
- 404 = group_id 错误;403 = apikey 不属于该组 owner。

### 3.3 GET /api/v1/groups/{group_id}/schemes — 取方案

响应 200 结构(字段契约):
```json
{
  "group_id": "4c777cadc3844a94",
  "company_name": "中国中铁",
  "meta": {"role": "承包商", "regions": [], "query_types": []},
  "status": "review",
  "schemes": [
    {
      "id": "Q0",                          // 方案编号,选择时用作 schemes 的键
      "name": "集团层",
      "region": "全语种",
      "lang": "中/英",
      "desc": "方案描述",
      "gaps": ["缺失项说明"],               // 字符串数组,可能为空
      "selected": false,                    // 当前方案级勾选态
      "tracks": [
        {
          "key": "全量新闻",                // 轨类型,固定6值之一,选择时用作 tracks 的键
          "boolean_query": "(\"中国中铁\" OR \"CREC\")",   // 布尔检索式
          "google_query": "(\"中国中铁\" (CREC))",          // Google检索式
          "sources": ["reuters.com", "bbc.com"],            // 信源域名白名单
          "frequency": "周级",              // 快讯|小时级|日级|周级|双周|月级
          "relevance": "direct",            // direct|indirect|context
          "selected": false                 // 当前轨级勾选态
        }
      ]
    }
  ],
  "keywords": [
    {"layer": "A", "category": "A1集团/公司名称簇", "terms": "\"中国中铁\" \"CREC\"", "lang": "全", "guard": "", "note": "说明"}
  ]
}
```
真实样例规模参考:4 个 schemes(Q0 集团层/Q1 东南亚项目群/Q2 波兰项目群/Q3 非洲项目群),每方案 2-3 tracks,keywords 15 条(A/B/C/D/R/X 六层)。

### 3.4 PUT /api/v1/groups/{group_id}/selection — 勾选

请求体(**覆盖式更新**,只传要改的键,未传的保持原值):
```json
{
  "schemes": {"Q0": true, "Q1": true},
  "tracks": {
    "Q0": {"全量新闻": true, "负面新闻": false},
    "Q1": {"全量新闻": true, "负面新闻": true, "行业新闻": true}
  }
}
```
约束:
- `schemes` 的键必须来自 3.3 返回的 `schemes[].id`;`tracks` 的轨键必须来自对应方案的 `tracks[].key`。**禁止编造 id/key**。
- 两个字段都必须传(可为空对象 `{}`)。

成功响应 200(真实样例):
```json
{"group_id":"4c777cadc3844a94","updated":true}
```
失败:409 = 已 commit 冻结。

### 3.5 POST /api/v1/groups/{group_id}/commit — 入库

无请求体。成功响应 200(真实样例):
```json
{"group_id":"4c777cadc3844a94","status":"committed"}
```
失败:409 = 重复 commit(视为已成功,不报错给用户)。
**注意**:commit 产生计费(1 单位),执行前必须已获得用户确认。

**额度规则(v1.24.0)**:提交任务时校验剩余额度(免费+付费)> 0,不足返回 403「额度不足,请联系管理员充值」。commit 扣 1 次,先扣免费额度,免费用完扣付费。查询额度:`GET /api/v1/billing/usage`(返回 free/paid 的 total/used/remaining + pending_count)。

### 3.6 GET /api/v1/groups/{group_id}/export — 导出 Excel

响应:二进制 xlsx(`Content-Type: application/octet-stream`,文件名 `<group_id>_tasks.xlsx`)。每个勾选的轨 = Excel 一行任务。无勾选轨时导出空任务表。保存文件并告知用户路径。

### 3.8 配额/资费接口(v1.24.0,管理员)

- `POST /api/v1/apikeys`:创建 apikey(默认免费 10/付费 0)。仅管理员。
- `PUT /api/v1/apikeys`:修改 apikey(旧 key→新 key,资费继承 + 历史迁移)。仅管理员。
- `DELETE /api/v1/apikeys/{apikey}`:删除(软删,数据保留)。仅管理员。
- `GET /api/v1/apikeys/list`:查所有普通用户额度。仅管理员。
- `GET /api/v1/apikeys/pending`:查当前 apikey 的 pending 任务。
- `POST /api/v1/billing/quota/paid` / `free`:增加付费/免费额度(apikey + count)。仅管理员。
- 管理员 apikey:额度 99999999,不受归属限制;普通用户只能查自己的 usage。

### 3.7 GET /health — 健康检查(无需鉴权)

```json
{"status":"ok"}
```
连接失败/非 200 时:重试 1 次,仍失败向用户报告服务不可用,**不要**继续后续步骤。

### 3.8 GET /api/v1/groups/{group_id}/status — 轻量运行状态

响应 200 字段契约:
```json
{
  "group_id": "4c777cadc3844a94",
  "status": "generating|review|committed|stopped",
  "running": true,            // 后台任务是否在本进程运行
  "current_step": 3,          // 当前执行步号;0=尚未开始;无 running 步=已出现的最大步号
  "total_steps": 6,           // 固定 6
  "steps_done": 2,            // done 步骤数
  "steps_error": 0,           // error 步骤数(单步失败不中断流程)
  "schemes_ready": false      // 【关键标识】true = 可以调 schemes
}
```
**判断 6 步跑完的唯一标准:`schemes_ready == true`**(等价 status ∈ {review, committed})。
status 从 generating → review 只发生在 6 步全部执行结束后。
`schemes_ready=false` 时禁止调 schemes。轮询建议用本接口(便宜),
需要步骤产物详情时再调 progress。

### 3.9 POST /api/v1/groups/{group_id}/stop — 停止任务

无请求体。仅 `status=generating` 可停;其他状态返回 409。

成功响应 200(真实样例):
```json
{"group_id":"4c777cadc3844a94","status":"stopped"}
```
约束:
- 停止前须获用户确认(停止 = 放弃本次生成的完整产物)。
- stopped 组**不可 commit**(409);pending 计费自动失效,不计费。
- 停止后如需完整方案:重新 POST /groups 提交新任务(同公司名可重复提交)。

## 4. 完整调用示例(Python,requests)

```python
import time
import requests

BASE = "http://10.33.17.72:8000"
HEADERS = {"Authorization": "Bearer <API_KEY>"}

# 1. 提交
r = requests.post(f"{BASE}/api/v1/groups", headers=HEADERS,
                  json={"company_name": "中国中铁", "role": "承包商"}, timeout=30)
r.raise_for_status()
group_id = r.json()["group_id"]

# 2. 轮询进度(10s 间隔,最多 25 分钟)
for _ in range(150):
    time.sleep(10)
    r = requests.get(f"{BASE}/api/v1/groups/{group_id}/progress", headers=HEADERS, timeout=30)
    r.raise_for_status()
    if r.json()["status"] != "generating":
        break
else:
    raise TimeoutError("流水线超时")

# 3. 取方案并全勾选(实际按业务规则选择)
r = requests.get(f"{BASE}/api/v1/groups/{group_id}/schemes", headers=HEADERS, timeout=30)
r.raise_for_status()
data = r.json()
selection = {
    "schemes": {sc["id"]: True for sc in data["schemes"]},
    "tracks": {sc["id"]: {t["key"]: True for t in sc.get("tracks", [])}
               for sc in data["schemes"]},
}

# 4. 勾选 → 入库 → 导出
r = requests.put(f"{BASE}/api/v1/groups/{group_id}/selection", headers=HEADERS,
                 json=selection, timeout=30)
r.raise_for_status()
r = requests.post(f"{BASE}/api/v1/groups/{group_id}/commit", headers=HEADERS, timeout=30)
r.raise_for_status()
r = requests.get(f"{BASE}/api/v1/groups/{group_id}/export", headers=HEADERS, timeout=60)
r.raise_for_status()
with open(f"{group_id}_tasks.xlsx", "wb") as f:
    f.write(r.content)
```

## 5. 错误处理规则

| HTTP | detail 样例 | 处理 |
|---|---|---|
| 400 | company_name 必填 | 参数错误,修正后重试 |
| 401 | apikey 无效 / 缺少 Authorization | 停止,向用户索要有效 apikey |
| 403 | 无权访问该方案组 | 停止,该组属于其他用户,不可重试 |
| 403 | 额度不足,请联系管理员充值(v1.24.0) | 免费+付费额度用尽,提示用户联系管理员 |
| 404 | 方案组不存在 | group_id 错误,停止 |
| 409 | 已入库 / 方案组已入库冻结 | 视为终态,不重试 selection/commit |
| 429 | 并发 pending 超限 | 等待用户处理未完成方案组后再试 |
| 5xx | — | 间隔 30s 重试,最多 3 次 |
| 网络超时 | — | 同 5xx 策略 |

## 6. 禁止事项

1. 禁止编造 group_id、scheme id、track key、apikey —— 全部来自服务端返回或用户提供。
2. 禁止在 `generating` 期间调 selection/commit。
3. 禁止 < 3 秒间隔轮询。
4. 禁止未经用户确认执行 commit(计费动作)。
5. 禁止修改已 `committed` 的组。
6. 本文档未定义的接口/字段一律不存在,不要猜测调用。

## 7. 验收清单(实现完成后自检)

- [ ] 无 apikey 时能明确向用户索要,不编造
- [ ] 提交后能轮询到 `review` 并打印各步状态
- [ ] selection 的键全部取自 schemes 实际返回
- [ ] commit 前有用户确认环节
- [ ] 409/403/404/429 各自有正确分支处理
- [ ] export 能保存 xlsx 文件并报告路径
