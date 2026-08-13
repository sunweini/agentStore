"""把文档块序列构造成章节树(设计 §4.1)。

输入 `(text, level)` 列表:level>=1 表示标题行(文本即标题),level=0 表示正文行(挂当前章)。
无标题时全部正文归入单章 `未命名章节`。输出有序 Chapter(title/level/order/text) 列表。
"""
from __future__ import annotations

from pydantic import BaseModel


class Chapter(BaseModel):
    title: str
    level: int
    order: int
    text: str


def build_chapters(blocks: list[tuple[str, int]]) -> list[Chapter]:
    chapters: list[Chapter] = []
    current: Chapter | None = None
    order = 0
    for text, level in blocks:
        text = text.strip()
        if not text:
            continue
        if level >= 1:
            order += 1
            current = Chapter(title=text, level=level, order=order, text="")
            chapters.append(current)
        else:
            if current is None:
                order += 1
                current = Chapter(title="未命名章节", level=0, order=order, text="")
                chapters.append(current)
            current.text = (current.text + "\n" + text).strip()
    return chapters
