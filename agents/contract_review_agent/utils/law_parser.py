"""法条源文件(md)解析为 LawArticle 列表。法条文本必须人工采集,本模块只解析不生成。"""
from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel

DOMAIN_ALIASES: dict[str, str] = {
    "劳动": "labor", "劳动合同": "labor",
    "买卖": "contract", "租赁": "contract", "承揽": "contract",
    "借款": "contract", "服务": "contract",
}
_DOMAIN_NAMES = {"labor": "劳动/劳动合同", "contract": "买卖/租赁/承揽/借款/服务"}
_ARTICLE_RE = re.compile(r"^##\s*(第[一二三四五六七八九十百零]+条)\s*$")
# 法规名标题:单个 `#` 后须有空白(`## 第X条` 交给 _ARTICLE_RE,避免被吞为 law_name)
_HEAD_RE = re.compile(r"^#\s+(.+)$")
_URL_RE = re.compile(r"^来源:\s*(\S+)$")
_DATE_RE = re.compile(r"^采集日期:\s*(\S+)$")
_DOMAIN_RE = re.compile(r"^领域:\s*(\S+)$")


class LawArticle(BaseModel):
    law_name: str
    article_no: str
    text: str
    source_url: str
    collected_date: str
    domain: str


def parse_law_md(md_text: str, default_domain: str = "contract") -> tuple[list[LawArticle], dict]:
    lines = md_text.splitlines()
    law_name, source_url, collected_date, domain = "", "", "", default_domain
    articles: list[LawArticle] = []
    errors: list[str] = []
    cur_no, buf = "", []
    for line in lines:
        if m := _ARTICLE_RE.match(line):
            if cur_no and buf:
                articles.append(LawArticle(
                    law_name=law_name, article_no=cur_no, text="".join(buf).strip(),
                    source_url=source_url, collected_date=collected_date, domain=domain,
                ))
            cur_no = m.group(1)
            buf = []
        elif m := _HEAD_RE.match(line):
            law_name = m.group(1).strip()
        elif m := _URL_RE.match(line):
            source_url = m.group(1).strip()
        elif m := _DATE_RE.match(line):
            collected_date = m.group(1).strip()
        elif m := _DOMAIN_RE.match(line):
            domain = m.group(1).strip()
        elif line.startswith("##"):
            # `##` 开头的标题但不是合法条号 → 非法条目,记原因并跳过(孤儿文本不入正文)
            errors.append(f"无法识别的条目标题: {line.strip()}")
        elif cur_no:
            buf.append(line)
    if cur_no and buf:
        articles.append(LawArticle(
            law_name=law_name, article_no=cur_no, text="".join(buf).strip(),
            source_url=source_url, collected_date=collected_date, domain=domain,
        ))
    if not law_name:
        errors.append("缺 law_name")
    for a in articles:
        if not a.text:
            errors.append(f"{a.article_no} 正文为空")
    meta = {"law_name": law_name, "source_url": source_url,
            "collected_date": collected_date or date.today().isoformat(),
            "domain": domain, "errors": errors, "count": len(articles)}
    return articles, meta
