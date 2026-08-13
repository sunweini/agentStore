# OCR 接线报告(Task 15:v0.5.0)

日期:2026-08-13
范围:仅 `agents/contract_review_agent/` + `tests/test_contract_review_agent.py`(禁改 sentiment/kingdee/common)。

## 目标

把百度云端 OCR 接到 parse 节点:扫描件(无文本层 pdf)不再直接返回 `needs_ocr`,
而是尝试 OCR 提取文本后照常分章;缺凭据 / OCR 失败给出明确错误码。

## 改动清单

### 1. `utils/ocr_client.py`

- **修复已知 bug**:`ocr_image_bytes` 的 `base64.b64encode(img)` → 加
  `.decode("ascii")`。百度 API 需要 base64 **字符串**,传 bytes 会 400。
- **新增 `get_token()`**(无参):从 `common.config.get_env` 读
  `BAIDU_OCR_API_KEY` / `BAIDU_OCR_SECRET_KEY`;缺任一返回空串(调用方据此报
  `ocr_unconfigured`);配了凭据则调 `get_baidu_token` 换 access_token。
  凭据只经 common.config 读取,不进 git 不进日志。

### 2. `graph/flows.py` `_parse_node`

`except NeedsOcrError` 分支不再 `return {"error": "needs_ocr"}`,改为调
新增的 `_ocr_parse(state)`:

1. `ocr_client.get_token()`:
   - 取 token 抛异常 → `ocr_failed`(结构化日志 `event=ocr_token_failed`,
     只记 error_type,不记 str(exc))
   - 返回空串(缺凭据)→ `ocr_unconfigured`(**不调 OCR**)
2. 有 token → `ocr_client.ocr_pdf_pages(file_path, token)`:
   - 抛异常 → `ocr_failed`(日志 `event=ocr_failed`,只记 error_type)
   - 返回空 / 全空白 → `ocr_failed`(日志 `error_type=empty`)
3. 有文本 → 按行 `_looks_like_heading` 启发式分章(标题行 level=1,正文 level=0,
   复用 document_parser 现有启发式与 `utils.chapterizer.build_chapters`)→
   `return {"chapters": [...]}`(与正常解析产物同构,后续 review 节点无需感知来源)

未做 OCR 文本质量调优(识别精度 / 更聪明分章为后续版本)。

### 3. `api.py` — 无需改动

确认:错误码路由已是通用 error 字符串透传(SSE `failed` 事件 data 与 result 端点
`{"error": t["error"]}` 原样输出),`ocr_unconfigured` / `ocr_failed` 直接透出前端,
无白名单 / 无特殊分支需要加。

### 4. 测试(`tests/test_contract_review_agent.py` 尾部追加,全 mock 不真调百度)

- `test_get_token_missing_creds_returns_empty`:缺凭据 → 空串,不发起网络请求
- `test_get_token_with_creds`:配凭据 → 换 token(mock httpx)
- `test_ocr_image_bytes_sends_base64_str`:**修复回归** —— 断言 `data["image"]`
  是 str 且 base64 解码还原原始 bytes
- `test_parse_node_ocr_with_token`:NeedsOcrError → mock token + mock OCR 文本 →
  产出 chapters(启发式分章,标题/正文归属正确)
- `test_parse_node_ocr_no_token`:无 token → `ocr_unconfigured`,且断言**未调**
  `ocr_pdf_pages`
- `test_parse_node_ocr_failed_on_exception`:OCR 抛异常 → `ocr_failed`
- `test_parse_node_ocr_failed_on_empty`:OCR 返回空白 → `ocr_failed`

mock 方式:monkeypatch `document_parser.parse_document` 恒抛 NeedsOcrError 触发
OCR 分支;monkeypatch `ocr_client.get_token` / `ocr_client.ocr_pdf_pages`
(flows 内函数体 import + 运行时属性查找,monkeypatch 生效)。

### 5. 文档同步

- `CLAUDE.md`:架构表 document_parser/ocr_client 行、常用操作"接百度 OCR(后续版本)"
  →"配百度 OCR"、约束大小限制、收尾版本号(现 v0.5.0 → 下版 v0.6)全部改为"已接线"。
- `API.md`:版本号 v0.4.0 → v0.5.0 + 版本历史加 v0.5.0 行;failed 错误码清单
  `needs_ocr` → `ocr_unconfigured / ocr_failed`;§5 性能参考改"扫描件自动走百度
  云端 OCR,缺凭据 ocr_unconfigured,失败 ocr_failed"。
- `CHANGELOG.md`:新增 v0.5.0 段;Follow-up 移除 OCR 接线条目,**只留非 JSON 重试**
  (v0.4.0 历史段保持原样,记录当时 pending 状态)。

## 测试结果

`python -m pytest tests/test_contract_review_agent.py -v`
**43 passed**(原 36 + 新增 7),全绿。

## 注意事项 / Concerns

- **OCR 分章质量依赖启发式**:`_looks_like_heading` 把 ≤30 字行当标题,OCR
  断行短句会被误判为章节标题;识别精度 / 分章调优留后续版本(本次按任务要求
  不做质量调优)。
- **PyMuPDF 依赖**:`ocr_pdf_pages` 未装 fitz 时返回 None → 归为 `ocr_failed`
  (而非 needs_ocr)。生产镜像 Dockerfile 不含 PyMuPDF,部署时需装
  `pymupdf`,否则扫描件会稳定 ocr_failed —— 已提示部署侧注意。
- **OCR 文本可能 > 5 万字限制**:OCR 文本经 build_chapters 后未再过
  `max_chars`(5 万字)校验(parse_document 的大小校验只在正常解析路径跑)。
  超长扫描件会进后续 LLM 审核,可能超上下文。可考虑在 `_ocr_parse` 内复跑
  大小校验(本次按任务范围未加)。
- **百度凭据未配时是稳定 ocr_unconfigured**:前端可据此提示"需管理员配置
  BAIDU_OCR_*";非 5xx 瞬时故障。
- 未真调百度 API(任务要求,测试全 mock);生产联调(真凭据 + 真实扫描件)为部署前置。
