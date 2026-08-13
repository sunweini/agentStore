"""百度智能云通用文字识别 API 封装(走云端接口,不下本地模型,主镜像零 OCR 依赖)。

待实现(设计 §4.1):
  - 读取 .env 的 BAIDU_OCR_API_KEY / BAIDU_OCR_SECRET_KEY(凭据不进 git)
  - 图片页上传百度 OCR → 识别文本 → 交 chapterizer 章节化
  - OCR 失败 → 抛 OCR_FAILED,提示重传清晰扫描件(设计 §8)
  - 用 httpx 调用,零新增模型依赖

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
