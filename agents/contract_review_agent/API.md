# 合同审核 Agent — 接口文档

版本:v0.5.0(2026-08-13)
部署:尚未上机生产(部署套件就绪,见 [deploy/README.md](deploy/README.md));本地/服务器 API 端口 `8000`。

## 0. 版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.5.0 | 2026-08-13 | **百度 OCR 接线**:扫描件(无文本层 pdf)自动走云端 OCR 分章后照常审核;缺凭据 `ocr_unconfigured`,失败 `ocr_failed`;`ocr_image_bytes` base64 bytes→str 修复 |
| v0.4.0 | 2026-08-13 | **文档收尾 + 部署套件 + minor 清理**:API 文档、Dockerfile/compose/deploy.sh/init_tables.sql、审核时间动态化、法条 source_url 修正 |
| v0.3.0 | 2026-08-13 | 内置法条种子(三法 294 条)+ 校验层运行时可用性加固(_exact 构造即加载 + seed 分批退避重试) |
| v0.2.1 | 2026-08-13 | **FastAPI 接口**:review/status/result/stop/prompt/laws/apikeys + health,SSE 章节进度 + 独立配额鉴权 |
| v0.2.0 | 2026-08-13 | 独立计费/鉴权/配额(contract_api_keys / contract_billing_records 表,与 sentiment 隔离) |
| v0.1.x | 2026-08-13 | 核心实现:法条库、文件解析、百度 OCR、章节审核节点、引用校验层、汇总报告、F1 prompt 优化、LangGraph 图 |
| v0.1.0 | 2026-08-13 | 项目初始化(目录骨架 + 占位文档 + langgraph 注册) |

## 1. 概述

双功能(F1 + F2),核心铁律 **反幻觉** —— 所有法律依据必须可回溯到法条库原文,不允许编造。

- **F1 审核 prompt 优化**:合同类型 + 原始审核 prompt → 结构化审核 prompt(产物可作 F2 审核要求复用)。
- **F2 章节审核**:上传 docx/pdf 合同 → 逐章审核,输出章节级报告,每处问题含
  **原文位置 / 问题描述 / 改进建议 / 法律依据**,法律依据经引用校验层逐条核验。

### 调用流程

```
POST /api/v1/contract/review   提交审核(multipart),SSE 流实时返回进度
  ↓ SSE:started → progress…(每 0.5s)→ done / failed / cancelled
GET  /api/v1/contract/result    取最终报告(JSON + markdown)
GET  /api/v1/contract/status    轻量轮询状态(可选)
POST /api/v1/contract/stop      停止任务(可选,不扣费)
POST /api/v1/contract/prompt    F1:优化审核 prompt(不计费)
```

### 鉴权

所有 `/api/v1/*` 接口需请求头 `apikey`(contract 独立体系,存 MySQL `contract_api_keys` 表):

```
apikey: <apikey>
```

apikey 由管理员通过 `POST /api/v1/apikeys` 创建(默认免费额度 10 次、付费 0 次)。apikey 决定**资源归属**(只能访问自己创建的任务)与**额度**(审核完成 commit 扣 1 单位)。

**管理员接口**(apikey 管理 3 接口)使用独立的 `admin` 请求头(填管理员自己的 apikey):

```
admin: <管理员apikey>
```

> 例外:`POST /api/v1/laws/upload` 也用 `apikey` 头,但校验的是**管理员** apikey。

### 通用约定

- 普通接口 Content-Type:`application/json; charset=utf-8`;`review` 为 `multipart/form-data` 提交、`text/event-stream` 响应
- 错误响应统一结构:`{"detail": "<中文错误描述>"}`
- 任务状态机:`running`(解析/审核中)→ `done`(成功 + 已扣费)/ `failed`(失败,不扣费)/ `cancelled`(被 stop,不扣费)

## 2. 接口详解

### 2.0 GET /health — 健康检查

无需鉴权。

**响应 200**(真实返回):
```json
{"status": "ok"}
```

---

### 2.1 POST /api/v1/contract/prompt — F1 审核 prompt 优化

合同类型 + 原始审核要求 → 结构化审核 prompt。**不计费**。

**请求**(`application/x-www-form-urlencoded`):

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `contract_type` | string | 是 | 合同类型(如 `劳动合同`/`买卖`/`租赁`) |
| `prompt` | string | 是 | 原始审核要求 |

```bash
curl -X POST http://localhost:8000/api/v1/contract/prompt \
  -H "apikey: <apikey>" \
  -d "contract_type=劳动合同&prompt=重点看违约金条款"
```

**响应 200**(真实结构):
```json
{
  "prompt": "你是劳动合同合同审核专家。\n一、角色:你是劳动合同合同审核专家,严格依据法律审核。\n二、审核范围:重点看违约金条款\n三、风险清单:逐条检查劳动合同合同常见风险(条款合法合规、双方权利义务对等、违约责任、争议解决)。\n四、输出格式:对每个问题给出【原文引用/风险类型/问题描述/改进建议/法律依据】;法律依据只允许引用法条库片段原文,禁止编造。\n五、引用指引:无法律依据时明确标注'仅提示,非强制'。"
}
```

**错误**:`401`(apikey 无效)、`422`(缺字段)

---

### 2.2 POST /api/v1/contract/review — 提交章节审核

上传 docx/pdf 合同文件,后台异步跑审核流水线(解析 → 逐章审核 → 引用校验 → 汇总),**SSE 实时推进度**。审核完成 **commit 扣 1 单位**。

**请求**(`multipart/form-data`):

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | file | 是 | 合同文件,仅 `.docx`/`.pdf`,**≤ 2MB** |
| `contract_type` | string | 是 | 合同类型(决定法条库领域过滤) |
| `prompt` | string | 是 | 审核要求(F1 产物或用户原始要求) |

```bash
curl -N -X POST http://localhost:8000/api/v1/contract/review \
  -H "apikey: <apikey>" \
  -F "file=@劳动合同.docx" \
  -F "contract_type=劳动合同" \
  -F "prompt=重点看违约金条款"
```

**响应 200**:SSE 流(`text/event-stream`),事件序列:

| 事件 | data | 说明 |
|---|---|---|
| `started` | 16 位 task_id | 任务创建成功,后续用此 id 查状态/结果 |
| `progress` | 0.0 ~ 1.0 | 每 0.5s 推一次进度 |
| `done` | `""` 或空 | 审核完成(已扣费) |
| `failed` | 错误码(如 `internal_error`) | 失败(不扣费) |
| `cancelled` | `""` | 被 stop 取消(不扣费) |

**SSE 响应示例**(真实返回):
```
event: started
data: 9f3c2b1a4d5e6f7a

event: progress
data: 0.0

event: progress
data: 0.5

event: done
data: 
```

**错误**(HTTP 非 200):
- `400 {"detail":"仅支持 docx/pdf"}`
- `400 {"detail":"文件超过 2MB 限制"}`
- `401 {"detail":"apikey 无效或已删除"}`
- `403 {"detail":"额度不足,请联系管理员充值"}`
- `429 {"detail":"并发 pending 超限,请先完成或取消未审核的任务"}`(同用户未完成 > 5)

---

### 2.3 GET /api/v1/contract/status — 任务状态

轻量轮询:状态 + 进度(仅本人任务)。

**请求**:`GET /api/v1/contract/status?task_id=<task_id>`,头 `apikey`

```bash
curl "http://localhost:8000/api/v1/contract/status?task_id=9f3c2b1a4d5e6f7a" \
  -H "apikey: <apikey>"
```

**响应 200**(真实返回):
```json
{"task_id": "9f3c2b1a4d5e6f7a", "status": "done", "progress": 1.0}
```

`status` 取值:`running` / `done` / `failed` / `cancelled`。

**错误**:`401`、`404 {"detail":"任务不存在"}`(他人任务与不存在同响应,不泄露)

---

### 2.4 GET /api/v1/contract/result — 最终报告

取审核结果(JSON + markdown 报告)。仅 `done` / `failed` 状态可取;进行中返回 409。

**请求**:`GET /api/v1/contract/result?task_id=<task_id>`,头 `apikey`

**响应 200 — done**(真实结构,内容节选):
```json
{
  "task_id": "9f3c2b1a4d5e6f7a",
  "status": "done",
  "result": {
    "report": "# 合同审核报告\n\n- 合同名称:劳动合同.docx\n- 审核依据:内置 v1\n- 审核时间:2026-08-13 17:30\n- 风险结论:高风险 1 处 / 中风险 0 处 / 提示 0 处\n\n## 高风险\n\n### 1.1 [第一章]\n**原文引用**:违约金每日 5%。\n**问题**:违约金比例可能超过法定上限。\n**建议**:调整至合理比例。\n**依据**:《中华人民共和国劳动合同法》第二十五条——\"除本法第二十二条和第二十三条规定的情形外,用人单位不得与劳动者约定由劳动者承担违约金。\"\n(法律依据已核验)\n",
    "report_json": {
      "chapter_reviews": [
        {
          "chapter": "第一章",
          "findings": [
            {
              "原文引用": "违约金每日 5%。",
              "风险类型": "合规",
              "问题描述": "违约金比例可能超过法定上限。",
              "改进建议": "调整至合理比例。",
              "法律依据": [
                {
                  "law_name": "中华人民共和国劳动合同法",
                  "article_no": "第二十五条",
                  "article_text": "除本法第二十二条和第二十三条规定的情形外,用人单位不得与劳动者约定由劳动者承担违约金。"
                }
              ],
              "confidence": "statutory"
            }
          ]
        }
      ],
      "stats": {"高风险": 1, "中风险": 0, "提示": 0}
    },
    "error": ""
  }
}
```

`findings[]` 字段:

| 字段 | 说明 |
|---|---|
| `原文引用` | 合同原文位置/段落 |
| `风险类型` | `合规` / `权益` / `漏洞` / `歧义` |
| `问题描述` | 问题说明(校验失败时追加 `(引用未能核验)`) |
| `改进建议` | 修改建议 |
| `法律依据` | `[{law_name, article_no, article_text}]`,已核验的逐字原文 |
| `confidence` | `statutory`(有已核验依据)/ `suggestion`(仅提示,无强制依据) |

**响应 200 — failed**:`result` 为 `{"error": "<错误码>"}`,如 `internal_error` / `too_long` / `unsupported` / `ocr_unconfigured` / `ocr_failed` / `billing_commit_failed`。

**错误**:`401`、`404`、`409 {"detail":"任务未完成"}`(running/cancelled 时取)

---

### 2.5 POST /api/v1/contract/stop — 停止任务

取消后台审核,释放并发额度,**不扣费**。仅本人任务,已终态拒绝。

**请求**:`POST /api/v1/contract/stop?task_id=<task_id>`,头 `apikey`

```bash
curl -X POST "http://localhost:8000/api/v1/contract/stop?task_id=9f3c2b1a4d5e6f7a" \
  -H "apikey: <apikey>"
```

**响应 200**(真实返回):
```json
{"ok": true}
```

**错误**:`401`、`404`、`409 {"detail":"任务已结束"}`(done/failed/cancelled 状态)

---

### 2.6 GET /api/v1/laws — 法条库列表

返回内置法条源 + 用户补充的**领域/条数/来源 URL**(来源 URL 为权威源采集地址,可回查)。

**请求**:`GET /api/v1/laws`,头 `apikey`

**响应 200**(真实返回,内置三法):
```json
{
  "laws": [
    {"law_name": "中华人民共和国劳动法", "domain": "labor", "count": 107, "source_url": "https://policy.mofcom.gov.cn/claw/clawContent.shtml?id=66953"},
    {"law_name": "中华人民共和国劳动合同法", "domain": "labor", "count": 98, "source_url": "https://policy.mofcom.gov.cn/claw/clawContent.shtml?id=4545"},
    {"law_name": "中华人民共和国民法典", "domain": "contract", "count": 89, "source_url": "http://www.bjrd.gov.cn/zyfb/zt/13jqgrd3chybjdbtzt/1303bjtwjbg/202101/t20210111_2214501.html"}
  ]
}
```

`domain` 取值:`labor`(劳动/劳动合同)、`contract`(买卖/租赁/承揽/借款/服务)。

**错误**:`401`

---

### 2.7 POST /api/v1/laws/upload — 用户补充法条库(管理员)

上传**文本法条**(md/txt 格式,`# 法规名` + `来源:` + `## 第X条` 结构),解析灌入向量库 + 精确索引;条号重复覆盖。**不计费**。

**请求**(`multipart/form-data`):字段 `file`(文本文件),头 `apikey`(必须是**管理员** apikey)

```bash
curl -X POST http://localhost:8000/api/v1/laws/upload \
  -H "apikey: <管理员apikey>" \
  -F "file=@补充法条.md"
```

**响应 200**(真实结构,来自 `seed()`):
```json
{"law_name": "某行业管理办法", "count": 15, "errors": []}
```

`errors`:解析告警列表(如非法条目标题、缺 law_name),空数组表示全部正常。

**错误**:`401`(apikey 无效)、`403 {"detail":"需要管理员权限"}`(非管理员)

---

### 2.8 POST /api/v1/apikeys — 创建 apikey(管理员)

创建后默认**免费额度 10 次、付费 0 次**;`name` 仅作创建时标签返回,不落库。

**请求**(`application/x-www-form-urlencoded`):字段 `name`,头 `admin`

```bash
curl -X POST http://localhost:8000/api/v1/apikeys \
  -H "admin: <管理员apikey>" \
  -d "name=财务部"
```

**响应 200**(真实返回):
```json
{"apikey": "sk-9f3c2b1a4d5e6f7a8b9c0d1e2f3a4b5c", "name": "财务部", "role": "normal", "free_quota": 10, "paid_quota": 0}
```

**错误**:`401`、`403 {"detail":"需要管理员权限"}`
(要创建 admin role 等扩展需直接改库,当前接口仅创建 normal 用户)

---

### 2.9 GET /api/v1/apikeys — apikey 额度列表(管理员)

返回全部 apikey(含已软删)的额度使用。

**请求**:`GET /api/v1/apikeys`,头 `admin`

**响应 200**(真实结构):
```json
{
  "apikeys": [
    {"apikey": "sk-9f3c2b1a4d5e6f7a", "role": "normal", "status": "active",
     "free": {"total": 10, "used": 1, "remaining": 9},
     "paid": {"total": 0, "used": 0, "remaining": 0}},
    {"apikey": "sk-aaaa...", "role": "admin", "status": "active",
     "free": {"total": 10, "used": 0, "remaining": 10},
     "paid": {"total": 0, "used": 0, "remaining": 0}}
  ]
}
```

**错误**:`401`、`403`

---

### 2.10 DELETE /api/v1/apikeys/{apikey} — 停用 apikey(管理员)

软删除(`status='deleted'`):该 apikey 立即无法调用任何接口,历史数据保留。**不可停用自己**。

**请求**:`DELETE /api/v1/apikeys/<apikey>`,头 `admin`

```bash
curl -X DELETE http://localhost:8000/api/v1/apikeys/sk-9f3c2b1a4d5e6f7a \
  -H "admin: <管理员apikey>"
```

**响应 200**(真实返回):
```json
{"ok": true}
```

**错误**:`401`、`403 {"detail":"需要管理员权限"}`、`403 {"detail":"不可停用自己"}`、`404 {"detail":"apikey 不存在"}`

---

## 3. 错误码汇总

| HTTP | detail | 场景 |
|---|---|---|
| 400 | 仅支持 docx/pdf | 上传非 docx/pdf 文件 |
| 400 | 文件超过 2MB 限制 | 文件超限 |
| 401 | apikey 无效或已删除 | apikey 未注册/已软删 |
| 403 | 需要管理员权限 | 非管理员调管理接口 |
| 403 | 额度不足,请联系管理员充值 | 免费+付费额度用尽 |
| 403 | 不可停用自己 | 管理员停用自己 |
| 404 | 任务不存在 | task_id 错误 / 他人任务(不泄露) |
| 404 | apikey 不存在 | 停用不存在的 apikey |
| 409 | 任务未完成 | 进行中取 result |
| 409 | 任务已结束 | 对已终态任务 stop |
| 429 | 并发 pending 超限,请先完成或取消未审核的任务 | 同用户未完成审核任务 > 5 |
| 422 | 缺字段 | 缺 contract_type/prompt/task_id/name 等 |

## 4. 计费规则

- **额度体系**:每个 apikey 免费额度(初始 10)+ 付费额度(当前默认 0);提交审核时校验剩余 > 0,不足 403
- **扣减时机**:`review` 后台审核完成(状态 → `done`)时 commit 扣 1 单位,先扣免费、免费用完扣付费,事务原子
- **并发**:同 apikey 最多 5 个 pending;stop / 失败 / 取消释放,不扣费
- **不计费**:F1 prompt 优化、法条查询/上传、失败、取消的任务

## 5. 性能参考

- 单次审核耗时取决于文件章节数与 LLM 响应,典型几十秒至数分钟
- `review` SSE 每 0.5s 推一次进度;也可用 `status`/`result` 轮询替代
- 文件解析上限:≤2MB 且正文 ≤5 万字(超限报错并提示分段;暂不支持超长文分段)
- 扫描件(无文本层 pdf)自动走**百度云端 OCR** 提取文本后照常审核:需在 `.env` 配 `BAIDU_OCR_API_KEY` / `BAIDU_OCR_SECRET_KEY`;未配凭据任务返回 `ocr_unconfigured`,OCR 取 token / 识别失败返回 `ocr_failed`
