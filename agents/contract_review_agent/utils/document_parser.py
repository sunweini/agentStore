"""文件解析层:docx/pdf → Document{chapters[]}(统一输出,设计 §4.1)。

待实现:
  - 数据模型:Chapter(title/level/order/text)+
    Document(chapters/total_chars/source_type: docx|pdf)
  - docx:python-docx 遍历段落,识别标题样式(Heading 1/2/3 或 w:outlineLvl),
    正文段落挂到最近章节
  - pdf:pypdf 提取文本层,按字号/行文启发式分章;
    无目录结构且无法可靠分章 → 降级单章全文,审核粒度仍可用
  - 无文本层 pdf:提取文本为空/过短 → 标记需 OCR(交 ocr_client.py)
  - 大小校验:解析前校验文件 ≤2MB;解析后校验总字数 ≤5 万字,
    超限报 CONTRACT_TOO_LONG(设计 §8)

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
