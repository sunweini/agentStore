"""utils 包:文件解析层工具。

- document_parser.py:docx(python-docx)+ pdf(pypdf 文本层)统一解析,
                     输出 Document{chapters[]}
- chapterizer.py:章节树构建(标题层级识别,无标题降级单章)
- ocr_client.py:百度智能云通用文字识别 API 封装(无文本层 pdf 走 OCR)

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
