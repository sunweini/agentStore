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
    "3. 法律依据只能从【法条片段】选取,字段 article_text 必须与片段原文一致;"
    "没有可依据的条款时,法律依据为空数组,confidence 填 suggestion。\n"
    "4. 有法律依据时 confidence 填 statutory。\n"
    "5. 只输出 JSON,格式: {\"chapter\": \"章标题\", \"findings\": [...]}。\n"
)


def _review_model():
    return get_chat_model().bind(temperature=0.1)


def review_chapter(llm, contract_type: str, chapter: dict,
                   review_prompt: str, law_store=None) -> dict:
    fragments = ""
    if law_store is not None:
        hits = law_store.retrieve(chapter.get("text", ""), contract_type, k=5)
        fragments = "\n".join(
            f"[{h['metadata'].get('law_name')} {h['metadata'].get('article_no')}] {h['text']}"
            for h in hits)
    user = f"审核要求:\n{review_prompt}\n\n合同章节:\n{chapter.get('text', '')}\n\n法条片段:\n{fragments or '(无)'}"
    resp = llm.invoke([
        {"role": "system", "content": _CHAPTER_SYSTEM},
        {"role": "user", "content": user},
    ])
    raw = resp.content if isinstance(resp.content, str) else json.dumps(resp.content)
    try:
        parsed = ChapterReview.model_validate_json(raw)
        return parsed.model_dump()
    except Exception as exc:
        return {"chapter": chapter.get("title", ""), "findings": [],
                "_error": f"bad_json: {exc}"}


def review_all(state: AgentState, law_store) -> dict:
    llm = _review_model()
    reviews = []
    for chapter in state["chapters"]:
        reviews.append(review_chapter(
            llm, state.get("contract_type", ""), chapter,
            state.get("review_prompt", ""), law_store))
    return {"chapter_reviews": reviews}
