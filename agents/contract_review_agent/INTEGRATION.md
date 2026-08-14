# INTEGRATION.md — AI Agent 对接规范(机器可读)

> 本文档供开发者的 AI agent 直接阅读并实现对接。契约完整自包含,无需其他上下文。
> 服务:合同审核 Agent(双功能 F1 审核 prompt 优化 + F2 章节级审核)。

## 0. 角色与目标

你是对接方 AI agent。你的任务:调用下述 HTTP API,完成「上传合同 → 章节级审核 → 取报告」全流程。严格遵守本文档的字段契约与状态机,不得臆造字段或接口。

两个独立功能,按需选用:

- **F1 审核 prompt 优化**(轻量、不计费):输入合同类型 + 原始审核要求,输出结构化审核 prompt,可直接作 F2 的 `prompt` 入参复用。
- **F2 章节审核**(计费):上传 docx/pdf 合同,后台异步跑「解析 → 逐章审核 → 引用校验 → 汇总」,产出章节级审核报告(每处问题含原文位置/问题描述/改进建议/法律依据,依据已核验可回溯)。

## 1. 基础契约

- Base URL:`http://10.33.17.72:8002`(生产)。路径前缀 `/api/v1`。
- 鉴权:每个 `/api/v1/*` 请求必须带 header `apikey: <API_KEY>`(**注意:header 名是 `apikey`,不是 `Authorization`**)。
  - `API_KEY` 由人工提供,不要编造;未提供时向用户索要。
- F2 上传是 `multipart/form-data`;其余请求/响应均为 UTF-8 JSON。
- 错误响应结构恒为 `{"detail": "<中文描述>"}`,HTTP 状态码见 §6。
- `task_id` 是 16 位十六进制字符串,由服务端生成,后续接口路径参数都用它。

## 2. F1 状态机(prompt 优化,同步)

无状态机,单次请求即返回:

```
POST /api/v1/contract/prompt
  {contract_type, prompt}
→ 200 {optimized_prompt}   // 直接当 F2 的 prompt 用
```

不计费、不鉴权限额(仍需 apikey header 验身份)。失败见 §6。

## 3. F2 状态机(章节审核,必须遵守)

两种用法任选:**SSE 流**(推荐,实时进度)或 **status/result 轮询**。

### 3.1 SSE 流(提交即返回事件流)

```
POST /api/v1/contract/review (multipart/form-data)
  file, contract_type, prompt
→ 200 text/event-stream:
    event: started   data: <task_id>
    event: progress  data: 0.0 ~ 1.0   (每 0.5s 推一次)
    event: done      data: ""          (完成,已扣费)
    event: failed    data: <error_code> (失败,不扣费)
    event: cancelled data: ""          (被 stop,不扣费)
```

收到 `started` 记下 task_id;收到 `done` 后调 `result` 取报告。

### 3.2 轮询(无 SSE 或需断点续查)

```
POST /api/v1/contract/review → 拿 task_id(SSE 的 started 事件,或见下)
GET  /api/v1/contract/status?task_id=...  → {status, progress}
GET  /api/v1/contract/result?task_id=...  → 报告(仅 done/failed 可取)
```

规则:
1. `status` 取值:`running` / `done` / `failed` / `cancelled`。
2. `running` 期间**禁止**调 result(409)。
3. `done` → 可调 result 取报告(已扣费);`failed` → result 里 `result.error` 是错误码;`cancelled` → 任务被 stop。
4. 轮询间隔 ≥ 1 秒(SSE 已 0.5s 推一次,轮询建议 2-5 秒)。单次审核典型几十秒至数分钟。
5. 停止任务(用户放弃):`POST /api/v1/contract/stop?task_id=...`,释放并发额度、不扣费。

## 4. 计费语义(实现前必读)

- **扣费时机**:审核完成(状态 → `done`)时扣 **1 单位**,先扣免费额度、免费用完扣付费额度,事务原子。
- **不计费**:F1 prompt 优化、法条查询/上传、失败、取消、stop 的任务。
- **额度校验**:提交 review 时校验剩余额度 > 0,不足 403。
- **并发**:同 apikey 最多 5 个 pending;stop/失败/取消释放。
- **额度体系**:每 apikey 免费(初始 10)+ 付费(充值)。查余额见 §5。

## 5. 资费查询接口(当前 apikey 自查)

`GET /api/v1/contract/status` 之外,余额查询走 admin 控制台或公共计费接口。当前 apikey 的额度/用量:

- 管理员接口:`GET /api/v1/apikeys`(header `apikey: <管理员key>`)返回该 agent 全部 key 额度。
- 统一接入指南(含跨 agent 统计、报表、admin 控制台)见仓库 `docs/superpowers/specs/` 下计费接入文档。

> 提示:普通 apikey 无法自查余额;如需自查能力,由对接方告知用户,或走 admin 控制台 `/admin/` 页面(超级管理员)。

## 6. 错误码汇总

| HTTP | 场景 |
|---|---|
| 400 | 参数/文件错误(不支持类型、超 2MB、contract_type/prompt 缺失) |
| 401 | apikey 无效或已删除 |
| 403 | 额度不足 / 非管理员调用管理接口 |
| 404 | task_id 不存在(他人任务与不存在同响应,不泄露) |
| 409 | 任务未完成时取 result / 任务已结束时 stop |
| 429 | 并发 pending 超限(同 apikey 未完成 > 5) |
| 500 | 内部错误(LLM/检索失败,error_code 见 result.error) |

`result.error` 错误码:`internal_error` / `too_long` / `unsupported` / `ocr_unconfigured` / `ocr_failed` / `billing_commit_failed`。

## 7. 大小限制与 OCR(实现前必读)

- 文件 ≤ **2MB** 且正文 ≤ **5 万字**,超限报 `too_long`(400)。
- 仅 `.docx` / `.pdf`;其他类型报 `unsupported`(400)。
- 扫描件(无文本层 pdf)自动走百度云端 OCR;`.env` 未配 OCR 凭据时任务返回 `ocr_unconfigured`,OCR 调用失败返回 `ocr_failed`。

## 8. 反幻觉契约(对接方应知晓)

审核报告的法律依据**必须已核验可回溯**:
- `confidence=statutory`:有逐字核验的法条原文(`法律依据[].article_text`)。
- `confidence=suggestion`:仅提示,无强制依据(原文标注"引用未能核验")。
- 报告 markdown 末尾声明法条库版本。
- 对接方展示结论时,若 `confidence=suggestion`,**不得**表述为"违反某法条",应表述为"提示/建议"。
