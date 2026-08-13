"""graph 包:章节审核流水线的状态模型与节点。

- state.py:  AgentState + finding 数据模型(AgentState / Finding / ChapterFinding)
- nodes.py:  解析 / 章节审核 / 汇总节点(LLM 审核,temperature 固定 0.1)
- verify.py: 引用校验层(核心反幻觉,纯代码无 LLM,保证确定性)
- flows.py:  图构建(parse → review_chapters → verify_refs → summarize)

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
