"""审核节点:每章检索法条 + LLM 判断(JSON, temperature=0.1)。"""
from __future__ import annotations

import json

from common.llm import get_chat_model
from agents.contract_review_agent.graph.state import AgentState, ChapterReview

_CHAPTER_SYSTEM = (
    "你是合同审核专家。严格遵守以下要求:\n"
    "1. 只能引用用户提供的【法条片段】中的条款,禁止引用片段外的任何法条,禁止编造。\n"
    "2. 对每个问题给出:原文引用(合同具体条款/段落)、风险类型(合规/权益/漏洞/歧义)、"
    "问题描述、改进建议、法律依据。\n"
    "3. 法律依据只能从【法条片段】选取,每项必须含三个字段:\n"
    "   - law_name:法律名称(与片段中的【名称】一致)\n"
    "   - article_no:条号(与片段中的条号一致)\n"
    "   - article_text:条文原文(必须与片段原文一致,不得改写)\n"
    "   没有可依据的条款时,法律依据为空数组,confidence 填 suggestion。\n"
    "4. 有法律依据时 confidence 填 statutory。\n"
    "5. 只输出 JSON,格式: {\"chapter\": \"章标题\", \"findings\": [...]}。\n"
)


def _review_model():
    return get_chat_model().bind(temperature=0.1)


def _norm_law_text(s: str) -> str:
    """归一化:去空白 + 全/半角标点统一。LLM 引文与片段原文常差在标点。"""
    import re
    return re.sub(r"[\s，。、；：！？,.;:!?（）()]+", "", s)


def _repair_refs(parsed: dict, hits: list[dict]) -> dict:
    """LLM 漏填 law_name/article_no 时,按 article_text 与片段原文匹配补齐。

    LLM 偶尔只回 article_text 不填 law_name/article_no,导致 schema 校验失败
    整章 findings 清空(静默漏审)。此修复是兜底:标点归一化匹配到片段即补全,
    保证 statutory 结论可进入校验层核验。
    """
    for finding in parsed.get("findings", []):
        for ref in finding.get("法律依据", []):
            if ref.get("law_name") and ref.get("article_no"):
                continue
            at = _norm_law_text(ref.get("article_text") or "")
            if not at:
                continue
            for h in hits:
                t = _norm_law_text(h.get("text", ""))
                if at == t or (at and at in t) or (t and t in at):
                    ref["law_name"] = h["metadata"].get("law_name")
                    ref["article_no"] = h["metadata"].get("article_no")
                    break
    return parsed


def review_chapter(llm, contract_type: str, chapter: dict,
                   review_prompt: str, law_store=None) -> dict:
    hits: list[dict] = []
    if law_store is not None:
        hits = law_store.retrieve(chapter.get("text", ""), contract_type, k=8)
        fragments = "\n".join(
            f"[{h['metadata'].get('law_name')} {h['metadata'].get('article_no')}] {h['text']}"
            for h in hits)
    else:
        fragments = ""
    user = f"审核要求:\n{review_prompt}\n\n合同章节:\n{chapter.get('text', '')}\n\n法条片段:\n{fragments or '(无)'}"
    resp = llm.invoke([
        {"role": "system", "content": _CHAPTER_SYSTEM},
        {"role": "user", "content": user},
    ])
    raw = resp.content if isinstance(resp.content, str) else json.dumps(resp.content)
    try:
        parsed = ChapterReview.model_validate_json(raw)
        return parsed.model_dump()
    except Exception:
        # schema 校验失败(常见:法律依据漏 law_name/article_no)→ 修复后重校验
        try:
            obj = json.loads(raw)
            return ChapterReview.model_validate(_repair_refs(obj, hits)).model_dump()
        except Exception as exc:
            return {"chapter": chapter.get("title", ""), "findings": [],
                    "_error": f"bad_json: {exc}"}


def review_all(state: AgentState, law_store) -> dict:
    llm = _review_model()
    reviews = []
    for chapter in state["chapters"]:
        # 空正文章节(如 PDF 标题启发式误判的孤立标题)无内容可审,跳过不进 LLM
        if not (chapter.get("text") or "").strip():
            continue
        reviews.append(review_chapter(
            llm, state.get("contract_type", ""), chapter,
            state.get("review_prompt", ""), law_store))
    return {"chapter_reviews": reviews}
