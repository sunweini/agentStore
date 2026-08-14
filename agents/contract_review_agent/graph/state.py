"""图状态与审核数据模型。"""
from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel


class LegalRef(BaseModel):
    law_name: str
    article_no: str
    article_text: str


class Finding(BaseModel):
    原文引用: str
    风险类型: Literal["合规", "权益", "漏洞", "歧义"]
    问题描述: str
    改进建议: str
    法律依据: list[LegalRef] = []
    confidence: Literal["statutory", "suggestion"] = "statutory"


class ChapterReview(BaseModel):
    chapter: str
    findings: list[Finding]


class AgentState(TypedDict):
    contract_type: str
    review_prompt: str
    _file_path: str
    _file_name: str
    _progress_cb: object = None  # 章节级进度回调(stage, current, total, title),None 跳过
    chapters: list[dict]
    chapter_reviews: list[dict]
    report: str
    report_json: dict
    error: str
