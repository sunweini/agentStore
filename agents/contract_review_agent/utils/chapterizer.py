"""章节树构建:标题层级识别 + 正文段落归属(设计 §4.1)。

待实现:
  - 识别标题(Heading 1/2/3 / w:outlineLvl),按 level 维护章节栈
  - 正文段落挂到最近章节;无标题结构的文档降级为单章全文
  - 输出有序 Chapter(title/level/order/text) 列表

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
