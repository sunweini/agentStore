"""graph 节点:parse / review_chapters / summarize(LLM 审核,temperature 固定 0.1)。

待实现(设计 §4.3/§4.5):
  - parse_node:文件解析层产物 → Document{chapters[]};
    大小校验(解析前 ≤2MB,解析后 ≤5 万字,超限报 CONTRACT_TOO_LONG)
  - review_chapters_node:逐章检索法条(域过滤 + BM25/embedding 混合 RRF,
    复用 common/rag.py)→ 注入审核 prompt → LLM 强制 JSON 输出 chapter_findings;
    只允许引用检索返回的法条片段,无命中标注"无相关法条",不编造
  - summarize_node:合并各章 findings → 按风险类型/严重度排序 →
    JSON + markdown 报告(报告头声明法条库版本 + 文件信息 + 审核时间,见设计 §6)

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
