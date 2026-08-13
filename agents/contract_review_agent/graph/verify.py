"""引用校验层:逐条核验法律依据可回溯源文件原文(纯代码,无 LLM)。

规则:
- 条号不存在 → 移除依据,该 finding 降级 suggestion,问题描述追加"(引用未能核验)"。
- 引文与库内原文不一致(ratio<0.8)→ 用库内原文替换,LLM 只定位不改写。

精确原文按 law_name + article_no 读 store/law_store.py 源文件 md,
不依赖向量近似;校验层为纯代码,无 LLM 参与。任何无法核验的内容
不进入 statutory 结论。

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md §4.4。
"""
from __future__ import annotations

import difflib

_THRESHOLD = 0.8


def _verify_ref(law_store, ref: dict) -> dict | None:
    exact = law_store.verify_ref(ref["law_name"], ref["article_no"])
    if exact is None:
        return None
    ratio = difflib.SequenceMatcher(None, ref.get("article_text", ""), exact).ratio()
    if ratio < _THRESHOLD:
        ref["article_text"] = exact
    return ref


def verify_reviews(chapter_reviews: list[dict], law_store) -> list[dict]:
    for review in chapter_reviews:
        for finding in review.get("findings", []):
            refs = finding.get("法律依据", [])
            kept = [r for r in (_verify_ref(law_store, r) for r in refs) if r is not None]
            if refs and not kept:
                finding["confidence"] = "suggestion"
                finding["问题描述"] = finding.get("问题描述", "") + " (引用未能核验)"
            finding["法律依据"] = kept
    return chapter_reviews
