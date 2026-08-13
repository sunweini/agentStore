"""graph 状态模型:AgentState + finding 数据模型。

待实现(设计 §4.3):
  - Finding:原文引用 / 风险类型(合规|权益|漏洞|歧义)/ 问题描述 / 改进建议 /
            法律依据 list[LawRef(law_name, article_no, article_text)] /
            confidence(statutory=有法律依据 | suggestion=仅提示)
  - ChapterFinding:chapter + findings[]
  - AgentState:文件元数据(名称/类型/大小)/ chapters / chapter_findings /
               verify_refs 校验结果 / 最终报告(JSON + markdown)

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
