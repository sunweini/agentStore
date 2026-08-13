"""任务/报告存储:JSON 文件库(复用 sentiment scheme_store 模式)。

待实现:
  - 任务状态记录(解析中/审核中/完成/失败 + 进度),供 status/result 接口查询
  - 报告落盘(结构化 JSON + markdown),按任务 id 组织
  - 并发安全:读-改-写加锁(线程锁 + fcntl 文件锁双保险,对齐 sentiment
    billing / scheme_store 模式)

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
