"""graph 引用校验层(核心反幻觉,纯代码无 LLM,保证确定性)。

待实现(设计 §4.4),对每条法律依据:
  1. 条号存在性:law_name + article_no 在库中查询,不存在 → 剔除/降级
  2. 引文一致性:LLM 输出 article_text 与库内原文做 fuzzy match
     (归一化后相似度阈值,如 difflib ratio ≥ 0.8),不一致 → 用库内原文替换
  3. 校验失败策略:
     - 条号不存在 → 该条法律依据移除,对应 finding 降级为 suggestion,
       标注"引用未能核验"
     - 引文不一致 → 替换为库内原文(LLM 只做定位,不做改写)

精确原文按 law_name + article_no 读 store/law_store.py 源文件 md,
不依赖向量近似;校验层为纯代码,无 LLM 参与。任何无法核验的内容
不进入 statutory 结论。

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
