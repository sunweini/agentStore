"""法条库:双存储职责分离(权威源文件 + 向量检索,设计 §4.2)。

待实现:
  - 法条源文件:data/laws/*.md(如 labor_law.md / labor_contract_law.md /
    civil_code_contract.md)。人工采集的权威原文,权威真源,可人工核对。
  - 向量库:Chroma data/contract-rag/,collection contract_law;
    seed 脚本解析 md(每条 = 条号 + 原文)灌库,每条元数据
    {law_name, article_no, source_url, collected_date}。语义检索用。
  - 查询两条路径:
    1. 语义检索(审核节点):域过滤(按 contract_type 限定法条集合)+
       BM25 + embedding 混合检索(RRF 融合,复用 common/rag.py)→ top-K
    2. 精确核验(校验层):按 law_name + article_no 读源文件 md 取精确原文
       → fuzzy match 核验 LLM 引文,不依赖向量近似
  - 法条按"条"粒度 embedding 入库(每条款一条向量),非整部法一条

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
