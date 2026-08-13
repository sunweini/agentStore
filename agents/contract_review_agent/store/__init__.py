"""store 包:法条库与任务/报告存储。

- law_store.py:法条库(源文件 md 权威真源 + Chroma 向量检索,设计 §4.2)
- task_store.py:任务/报告存储(JSON 文件库,复用 sentiment scheme_store 模式)

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
