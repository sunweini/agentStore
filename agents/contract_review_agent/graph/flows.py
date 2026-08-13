"""graph 图构建:build_graph() 编排节点边(与 sentiment 同风格)。

图结构(设计 §3):
  START → parse → review_chapters → verify_refs → summarize → END

待实现:
  - build_graph():StateGraph 顺序边;单步失败标 error 不中断
    (参考 sentiment agents/sentiment_query_agent/graph/flows.py)
  - recursion_limit 为运行时 config 参数(graph.invoke(config=...)),非 compile 参数

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
