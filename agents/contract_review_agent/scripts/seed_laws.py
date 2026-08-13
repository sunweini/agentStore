"""法条灌库脚本:data/laws/*.md → 解析为条目(条号 + 原文)→ 灌入 Chroma。

待实现(设计 §4.2):
  - 解析 md:每条 = 条号 + 原文,元数据 {law_name, article_no, source_url,
    collected_date};law_name + article_no 唯一,条号重复则覆盖
  - 逐条记录来源 URL + 采集日期(权威来源,可人工核对)
  - 内置种子(第一版):劳动法全文(107 条)/ 劳动合同法全文(98 条)/
    民法典合同编高频条款(~100 条)。法条文本**人工从权威来源采集**
    (国家法律法规数据库 flk.npc.gov.cn、全国人大官网),
    严禁 LLM 生成/记忆填充
  - 支持 --dry-run 预览灌入内容

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
