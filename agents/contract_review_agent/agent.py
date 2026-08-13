"""contract-review-agent 图构建入口:章节流水线(parse → review_chapters → verify_refs → summarize)。

图结构(设计 §3,与 sentiment 同风格 LangGraph 循环图):
  START → parse → review_chapters → verify_refs → summarize → END
  parse:          文件解析层(docx / pdf 文本层 / 无文本层 OCR 标记)→ Document{chapters[]}
  review_chapters:逐章检索法条 → LLM(temperature=0.1)审核 → chapter_findings
  verify_refs:    引用校验层(核心反幻觉):条号存在 + 引文 fuzzy match,失败降级
  summarize:      合并 findings → 风险排序 → JSON + markdown 报告(声明法条库版本)

待实现:
  - build_graph():LangGraph 图构建入口(节点见 graph/nodes.py、flows.py)
  - langgraph.json 注册入口: "./agents/contract_review_agent/agent.py:build_graph"
  - 运行时 checkpointer / SSE 章节进度(参考 sentiment run_pipeline 模式)

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
