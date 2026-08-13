"""contract-review-agent FastAPI 接口层(独立 apikey 配额计费,与 sentiment 隔离)。

接口清单(设计 §7):
  POST /api/v1/contract/review   上传文件 + contract_type + 审核要求 → task_id(SSE 章节进度)
  GET  /api/v1/contract/status   任务状态(解析中/审核中/完成/失败 + 进度)
  GET  /api/v1/contract/result   最终报告(JSON + markdown)
  POST /api/v1/contract/stop     停止任务(复用 sentiment stop 模式)
  POST /api/v1/contract/prompt   F1:合同类型 + 原始 prompt → 优化后 prompt
  POST /api/v1/laws/upload       用户补充法条库
  GET  /api/v1/laws              法条库列表(law_name/条数/版本)
  POST /api/v1/apikeys           独立 apikey 管理(创建/修改/删除,管理员)

待实现:
  - create_app():FastAPI 应用,multipart 上传 ≤2MB,鉴权走 auth.py(独立 apikey 体系)
  - 错误处理映射:CONTRACT_TOO_LONG / UNSUPPORTED_TYPE / OCR_FAILED(设计 §8)

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
