"""汇总:风险分级 + markdown 报告 + 结构化 JSON。"""
from __future__ import annotations

from collections import Counter

RISK_ORDER = {"高风险": 0, "中风险": 1, "提示": 2}


def risk_level(finding: dict) -> str:
    if finding.get("confidence") == "suggestion":
        return "提示"
    return {"合规": "高风险", "权益": "中风险", "漏洞": "中风险", "歧义": "中风险"}.get(
        finding.get("风险类型"), "提示")


def _finding_block(seq: int, finding: dict) -> str:
    refs = finding.get("法律依据", [])
    ref_lines = "\n".join(
        f"**依据**:《{r['law_name']}》{r['article_no']}——\"{r['article_text']}\""
        for r in refs)
    note = "(法律依据已核验)" if refs else "(无法律依据,仅提示,非强制)"
    return (
        f"### {seq}.1 [{finding.get('章节', '')}]\n"
        f"**原文引用**:{finding.get('原文引用', '')}\n"
        f"**问题**:{finding.get('问题描述', '')}\n"
        f"**建议**:{finding.get('改进建议', '')}\n"
        + (ref_lines + "\n" if ref_lines else "") + f"{note}\n")


def build_report(chapter_reviews: list[dict], meta: dict) -> str:
    grouped: dict[str, list[tuple[str, int, dict]]] = {"高风险": [], "中风险": [], "提示": []}
    seq = 0
    for review in chapter_reviews:
        for finding in review.get("findings", []):
            seq += 1
            finding = dict(finding, 章节=review.get("chapter", ""))
            grouped[risk_level(finding)].append((review.get("chapter", ""), seq, finding))
    stats = Counter(risk_level(f) for r in chapter_reviews
                    for f in r.get("findings", []))
    lines = [
        "# 合同审核报告",
        "",
        f"- 合同名称:{meta.get('合同名称', '')}",
        f"- 审核依据:{meta.get('法条库版本', '')}",
        f"- 审核时间:{meta.get('审核时间', '')}",
        f"- 风险结论:高风险 {stats['高风险']} 处 / 中风险 {stats['中风险']} 处 / 提示 {stats['提示']} 处",
        "",
    ]
    for level in ("高风险", "中风险", "提示"):
        items = grouped[level]
        if not items:
            continue
        lines.append(f"## {level}")
        lines.append("")
        for chapter, seq, finding in items:
            lines.append(f"### {seq}.1 [{chapter}]")
            lines.append(f"**原文引用**:{finding.get('原文引用', '')}")
            lines.append(f"**问题**:{finding.get('问题描述', '')}")
            lines.append(f"**建议**:{finding.get('改进建议', '')}")
            for r in finding.get("法律依据", []):
                lines.append(f"**依据**:《{r['law_name']}》{r['article_no']}——\"{r['article_text']}\"")
            note = "(法律依据已核验)" if finding.get("法律依据") else "(无法律依据,仅提示,非强制)"
            lines.append(note)
            lines.append("")
    return "\n".join(lines)


def build_report_json(chapter_reviews: list[dict]) -> dict:
    stats = Counter(risk_level(f) for r in chapter_reviews
                    for f in r.get("findings", []))
    return {
        "chapter_reviews": chapter_reviews,
        "stats": {"高风险": stats["高风险"], "中风险": stats["中风险"], "提示": stats["提示"]},
    }
