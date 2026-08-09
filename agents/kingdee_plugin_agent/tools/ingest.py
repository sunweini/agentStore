"""RAG 导入管线:URL/目录 → 清洗 → 代码感知分块 → RagClient 入库。

入口(两种):
  - ingest_url(url, collection, title="")  单页导入,返回新增 chunk 数
  - ingest_dir(dir, collection)            目录 *.md 批量导入,返回总新增 chunk 数
  - CLI: python -m agents.kingdee_plugin_agent.tools.ingest
        --url <url> --collection guide|api_ref [--title X]
        --dir <path> --collection ...
        --seed-internal --collection guide   # 内部 skill 文档(skills/**/*.md)

设计决策(零新增依赖):
  - HTTP 用 httpx(项目已有依赖),超时 30s + 浏览器 User-Agent;
  - HTML→文本用 stdlib html.parser(环境未装 beautifulsoup4,不为 HTML 提取
    引入新依赖;实测 developer.kingdee.com 页面纯解析即可拿到正文);
  - 分块:代码感知 —— 段落边界切分,max_chars 超限才切,代码围栏(```)无论
    多长整体独占一个 chunk,绝不在围栏内部切分;
  - 存储:common/rag.py RagClient(api_ref/guide/experience 三库)。
  - 幂等为**去重式**:按 metadata.source + 文本查重,同 source 且内容未变的
    重跑新增 0;内容变更后重跑会新增(新旧版本并存),须先
    `--delete-source <source>` 删旧再重灌(对齐 knowledge-steward 维护手册
    "文档导入幂等"约定 —— 幂等仅对未变更内容成立)。

错误语义:
  - 单 URL 失败(HTTP 错误/超时/无正文)→ IngestError(CLI 打印明确信息,
    --url 全部失败时退出码 1);
  - 批量模式(--dir / 多 URL):单条失败 log + 继续,全部失败才整体报错
    ("Never silently skip everything")。

设计文档: docs/superpowers/plans/rag-ingest-report.md
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import httpx

from common.rag import RagClient, RAG_COLLECTIONS

logger = logging.getLogger(__name__)

#: 抓取超时(秒)与 User-Agent(部分站点对默认 UA 有反爬)
FETCH_TIMEOUT = 30
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 agentStore-rag-ingest/1.0"

#: 正文按块换行分隔的标签(HTML→文本时在其前后加换行,保留段落结构;pre 单独处理)
_BLOCK_TAGS = {
    "p", "div", "section", "article", "li", "ul", "ol", "dl", "dt", "dd",
    "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "blockquote",
    "br", "hr", "figure", "figcaption", "summary", "details",
}
#: 直接丢弃的噪音标签(script/nav 导航/页头页脚/表单)
_SKIP_TAGS = {"script", "style", "noscript", "iframe", "nav", "header", "footer", "form", "template"}

#: 行级样板噪音(分享/收藏/评论/翻页/导航/作者信息/交互按钮等,命中整行即剔除)
#: 注意:以下均为**动态或交互内容**,两次抓取/不同用户状态文本不同,不剔除会
#: 导致同 URL 重灌时文本差异、重复入库 —— 必须兜住:
#:   - 浏览/赞赏计数(裸数字行 "4,457"、 "N次浏览"、"N人赞赏了该文章");
#:   - 点赞/删除/收起/取消/更多 交互按钮行;
#:   - 编辑于/发布于 时间戳(随编辑行为变化)。
_BOILERPLATE_RE = re.compile(
    r"^\s*(?:分享|收藏|评论|点赞|举报|订阅|关注|转发|下载|复制|打印"
    r"|返回(?:顶部|列表|首页)|上一篇|下一篇|上一页|下一页"
    r"|相关(?:文章|推荐|阅读|内容)|热门(?:文章|推荐|标签)|最新(?:文章|动态)"
    r"|阅读量[:：]?\s*[\d,]*|浏览量[:：]?\s*[\d,]*|[\d,]*次浏览|[\d,]*人(?:赞赏|点赞)了该文章"
    r"|发布时间[:：][^\n]*|更新时间[:：][^\n]*|编辑于[^\n]*|发布于[^\n]*|未经作者许可[^\n]*"
    r"|原创|所属(?:产品|领域|云/领域)[:：][^\n]*"
    r"|赞|删除|收起|取消|更多|首页|登录|注册|立即下载|扫码(?:关注|下载)|微信|QQ 群"
    r"|[\d,]+(?:\.\d+)?万?)"
    r"[\s·•—|]*$"
)

#: 站点重发布标题前缀(如 "【第36期】 xxx"):剥离前缀使正文不随期数变化
#: (重发布仅换期数时,正文文本保持稳定,重灌 +0)。
_ISSUE_PREFIX_RE = re.compile(r"^【第\s*\d+\s*期】\s*")

#: YAML frontmatter(文件首行 --- 到下一个 ---)
_FRONTMATTER_RE = re.compile(r"^﻿?---\s*\n.*?\n---\s*\n", re.S)

#: 标题里的站点名后缀(仅剥离已知站点名,如 "X - 金蝶开发者社区" → "X")
#: 注意:不能按任意分隔符截断 —— "金蝶云·星空-BOS平台"、"话题详情-财务IT"
#: 中的 - / · / | 都是合法标题字符,只有已知站点名才是可剥离的后缀。
_SITE_SUFFIX_RE = re.compile(
    r"\s*(?:[-—|_]\s*)?(?:金蝶开发者社区|金蝶云社区官网|金蝶云社区|金蝶社区|开发者社区)\s*$"
)


class IngestError(RuntimeError):
    """导入失败(HTTP 错误/超时/无正文/目录无文件等),消息面向 CLI 用户。"""


# ---------------------------------------------------------------------------
# HTML → 文本
# ---------------------------------------------------------------------------

class _HtmlToText(HTMLParser):
    """轻量 HTML→正文提取:丢弃 script/style/nav 等,块级标签转段落换行。

    parts 元素为 (kind, text):
      - ("n", data):普通文本(装配时按行折叠空白 + 剔样板行);
      - ("p", data):<pre> 内数据,装配时按行**原样保留**(代码缩进/结构不折叠);
      - ("s", ""):块级标签产生的段落分隔(装配时输出一个空行 —— 段落边界)。
    未闭合 <pre> 的毒化兜底:后续遇到任何非 pre 块级标签(开始/结束)即退出
    pre 模式,后续文本回归正常清洗。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_pre = False
        self._parts: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "pre":
            if not self._in_pre:
                self._parts.append(("s", ""))  # pre 块前留段落分隔
            self._in_pre = True
        elif tag in _BLOCK_TAGS:
            self._in_pre = False  # 未闭合 <pre> 毒化兜底:新块级标签退出 pre 模式
            self._parts.append(("s", ""))

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "pre":
            self._parts.append(("p", "\n"))  # pre 块后留段落分隔(原样空行)
            self._in_pre = False
        elif tag in _BLOCK_TAGS:
            self._in_pre = False  # 未闭合 <pre> 毒化兜底
            self._parts.append(("s", ""))

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._parts.append(("p", data) if self._in_pre else ("n", data))


def _assemble_text(parts: list[tuple[str, str]]) -> str:
    """按行装配:
    - ("n") 行:折叠空白 + 剥【第N期】前缀 + 剔样板行;
    - ("p") 行:原样保留(代码缩进/结构);
    - ("s") 段落分隔:输出空行(段落边界不丢失,分块粒度正常);
    - 连续空行收敛到 ≤2(段落分隔,代码块内空行最多留 2 行)。"""
    lines: list[str] = []
    for kind, data in parts:
        if kind == "p":
            lines.extend(data.split("\n"))
        elif kind == "s":
            lines.append("")
        else:  # "n"
            for raw in data.split("\n"):
                line = " ".join(raw.split())
                if not line:
                    continue
                line = _ISSUE_PREFIX_RE.sub("", line, count=1)  # 重发布期数前缀剥离
                if not line or _BOILERPLATE_RE.match(line):
                    continue
                lines.append(line)
    out: list[str] = []
    blanks = 0
    for line in lines:
        if not line.strip():
            blanks += 1
            if blanks > 2:
                continue
        else:
            blanks = 0
        out.append(line)
    return "\n".join(out).strip()


def html_to_text(html: str) -> str:
    """HTML → 正文文本:剔除 script/style/nav 噪音,块级标签转段落换行
    (段落空行保留),<pre> 代码行**原样保留**缩进/结构,非代码行折叠空白、
    剥重发布期数前缀并剔样板行。"""
    parser = _HtmlToText()
    try:
        parser.feed(html)
    except Exception as exc:  # 解析容错:个别畸形 HTML 不阻塞导入
        logger.warning("HTML 解析异常(已忽略,尽量保留已提取文本): %s", exc)
    return _assemble_text(parser._parts)


def clean_text(text: str) -> str:
    """通用文本清洗:行内空白折叠、整行样板噪音剔除、连续空行收敛。

    注意:html_to_text 已按 <pre> 感知完成等价清洗(代码行原样保留),
    HTML 导入路径直接用 html_to_text 输出、**不要再过 clean_text**,否则
    代码缩进会被折叠;本函数面向纯文本/非代码场景。
    """
    lines = []
    for raw in text.split("\n"):
        line = " ".join(raw.split())
        if not line or _BOILERPLATE_RE.match(line):
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ---------------------------------------------------------------------------
# 代码感知分块
# ---------------------------------------------------------------------------

#: 句末标点(长段落按句切分的边界)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?;；])\s*")


def _split_long_paragraph(content: str, max_chars: int) -> list[str]:
    """超长段落按句末标点切分到 max_chars 内(段落内无标点时整体保留)。

    代码围栏是独立块、不走此路径 —— 围栏永远整块保留。
    """
    if len(content) <= max_chars:
        return [content]
    parts: list[str] = []
    cur = ""
    for piece in _SENTENCE_SPLIT_RE.split(content):
        if not piece:
            continue
        if cur and len(cur) + len(piece) > max_chars:
            parts.append(cur)
            cur = piece
        else:
            cur += piece
    if cur:
        parts.append(cur)
    return parts or [content]


def code_aware_chunk(text: str, max_chars: int = 1500) -> list[str]:
    """按段落边界分块,代码围栏(```)整体保留在单一 chunk。

    - 段落 = 以空行分隔的连续行块;
    - 普通段落累积到 max_chars 超限时切块(块间以空行连接);单个段落本身
      超过 max_chars 时按句末标点(。！？!?;；)切分 —— 段落边界优先,
      长段落兜底;
    - 代码围栏(``` 开头到下一个 ``` 结尾)无论多长、中间有多少空行,
      整体独占一个 chunk,绝不在围栏内部切分;
    - 未闭合围栏按代码块整体保留(不丢内容)。
    """
    lines = text.split("\n")
    blocks: list[tuple[str, bool]] = []  # (内容, is_code)

    para: list[str] = []
    code_lines: list[str] = []
    in_code = False

    def flush_para() -> None:
        nonlocal para
        if para:
            content = "\n".join(para)
            if content.strip():  # 全空白段落不产出;保留行首缩进(代码行)
                blocks.append((content, False))
            para = []

    def flush_code() -> None:
        nonlocal code_lines, in_code
        if code_lines:
            blocks.append(("\n".join(code_lines).strip(), True))
            code_lines = []
        in_code = False

    for line in lines:
        if line.strip().startswith("```"):
            if in_code:
                code_lines.append(line)
                flush_code()
            else:
                flush_para()  # 围栏前的半截段落先出块
                code_lines.append(line)
                in_code = True
            continue
        if in_code:
            code_lines.append(line)  # 围栏内空行也原样保留
            continue
        if line.strip():
            para.append(line)
        else:
            flush_para()
    flush_para()
    if in_code:  # 未闭合围栏:整段按代码块保留
        flush_code()

    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0

    def emit() -> None:
        nonlocal cur, cur_len
        if cur:
            chunks.append("\n\n".join(cur))
            cur, cur_len = [], 0

    for content, is_code in blocks:
        if is_code:
            emit()  # 代码块独立成 chunk,前面若有积压文本先出块
            chunks.append(content)
            continue
        for piece in _split_long_paragraph(content, max_chars):
            if cur and cur_len + len(piece) + 2 > max_chars:
                emit()
            cur.append(piece)
            cur_len += len(piece) + 2
    emit()
    return chunks


# ---------------------------------------------------------------------------
# 标题提取
# ---------------------------------------------------------------------------

def _title_from_html(html: str) -> str:
    """<title> 标签优先,退化为首个 <h1>;结果清理站点名后缀与空白。"""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1))
        title = _SITE_SUFFIX_RE.sub("", title).strip()  # 站点名后缀剥离
        title = _ISSUE_PREFIX_RE.sub("", title, count=1)  # 重发布期数前缀剥离
        if title:
            return title
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if title:
            return title
    return ""


def _title_from_url(url: str) -> str:
    """URL 兜底:取路径最后一段(去查询串/后缀)。"""
    path = url.split("?", 1)[0].rstrip("/")
    seg = path.rsplit("/", 1)[-1]
    return re.sub(r"\.[a-z0-9]{2,5}$", "", seg) or url


def normalize_title(url: str, html: str | None = None) -> str:
    """URL → 标题:<title> 优先 → 首个 <h1> → URL 尾段。

    html 为空时自动抓取(独立调用场景);ingest_url 内部复用已抓取的 html,
    避免同一 URL 抓两次。
    """
    if html is None:
        html = fetch_html(url)
    return _title_from_html(html) or _title_from_url(url)


# ---------------------------------------------------------------------------
# 抓取与元数据
# ---------------------------------------------------------------------------

def fetch_html(url: str, timeout: float = FETCH_TIMEOUT) -> str:
    """抓取页面 HTML。HTTP 错误/超时/网络异常 → IngestError(明确消息)。"""
    try:
        resp = httpx.get(
            url, timeout=timeout, follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
    except httpx.TimeoutException:
        raise IngestError(f"请求超时({timeout:.0f}s): {url}") from None
    except httpx.HTTPStatusError as exc:
        raise IngestError(f"HTTP {exc.response.status_code}: {url}") from None
    except httpx.TransportError as exc:
        raise IngestError(f"网络错误({type(exc).__name__}): {url}") from None
    return resp.text


def strip_frontmatter(text: str) -> str:
    """去除 markdown 文件首部的 YAML frontmatter(无则原样返回)。"""
    return _FRONTMATTER_RE.sub("", text, count=1)


def _existing_texts(client: RagClient, collection: str, source: str) -> set[str]:
    """同 source 已入库的文本集合(幂等查重用)。"""
    store = client._store(collection)
    found = store.get(where={"source": source})
    return set(found.get("documents") or [])


def _title_from_md(text: str, filename: str) -> str:
    """markdown 标题:首个 # 标题,退化文件名。"""
    for line in text.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return filename


# ---------------------------------------------------------------------------
# 导入入口
# ---------------------------------------------------------------------------

def _client(data_dir: Path | None) -> RagClient:
    return RagClient(data_dir=data_dir) if data_dir else RagClient()


def delete_source(collection: str, source: str, data_dir: Path | None = None) -> int:
    """删除某 source 的全部条目(内容变更后"删旧重灌"的前置操作,防新旧版本并存)。

    返回删除条数;source 不存在返回 0。
    """
    client = _client(data_dir)
    store = client._store(collection)
    found = store.get(where={"source": source})
    ids = found.get("ids") or []
    if ids:
        store._collection.delete(ids=ids)
    return len(ids)


def ingest_url(url: str, collection: str, title: str = "", data_dir: Path | None = None) -> int:
    """单页导入:抓取 → 清洗 → 代码感知分块 → 入库(metadata: source/title/collection)。

    返回新增 chunk 数。**去重式幂等**:同 source 且文本未变的重跑返回 0;
    内容变更后需先 delete_source 再重灌(否则新旧版本并存)。HTTP 错误/超时/
    无正文抛 IngestError。
    """
    if collection not in RAG_COLLECTIONS:
        raise IngestError(f"未知库: {collection}(可选 {', '.join(RAG_COLLECTIONS)})")
    client = _client(data_dir)
    html = fetch_html(url)
    if not title:
        title = _title_from_html(html) or _title_from_url(url)
    text = html_to_text(html)  # 已按 <pre> 感知清洗,勿再过 clean_text(会折叠代码缩进)
    if not text:
        raise IngestError(f"页面无正文可提取: {url}")
    chunks = code_aware_chunk(text)
    existing = _existing_texts(client, collection, url)
    docs = [c for c in chunks if c not in existing]
    if docs:
        client.add_documents(
            collection, docs,
            [{"source": url, "title": title, "collection": collection}] * len(docs),
        )
    logger.info("[ingest] %s → %s: 分块 %d,新增 %d(重 %d)", url, collection, len(chunks), len(docs), len(chunks) - len(docs))
    return len(docs)


def _ingest_md_file(path: Path, collection: str, root: Path, data_dir: Path | None) -> int:
    """单文件导入:读 → 去 frontmatter → 分块 → 入库(source = 相对路径)。"""
    client = _client(data_dir)
    text = strip_frontmatter(path.read_text(encoding="utf-8"))
    if not text.strip():
        return 0
    chunks = code_aware_chunk(text)
    source = str(path.relative_to(root))
    title = _title_from_md(text, path.stem)
    existing = _existing_texts(client, collection, source)
    docs = [c for c in chunks if c not in existing]
    if docs:
        client.add_documents(
            collection, docs,
            [{"source": source, "title": title, "collection": collection}] * len(docs),
        )
    logger.info("[ingest] %s → %s: 分块 %d,新增 %d", source, collection, len(chunks), len(docs))
    return len(docs)


def ingest_dir(dir: Path, collection: str, data_dir: Path | None = None) -> int:
    """目录批量导入:递归 *.md + *.cs(代码文件,代码感知分块),逐文件处理;单文件失败 log + 继续,
    全部失败才抛 IngestError(不静默全跳过)。返回总新增 chunk 数。

    幂等为**去重式**:文件未变重跑新增 0;编辑已灌入的文件后重跑会新增
    (旧版+新版并存),需先 delete_source(source=相对路径) 再重灌。
    """
    if collection not in RAG_COLLECTIONS:
        raise IngestError(f"未知库: {collection}(可选 {', '.join(RAG_COLLECTIONS)})")
    files = sorted([f for f in Path(dir).rglob("*") if f.suffix.lower() in (".md", ".cs") and f.is_file()])
    if not files:
        raise IngestError(f"目录无 markdown 文件: {dir}")
    total, failed = 0, []
    for f in files:
        try:
            total += _ingest_md_file(f, collection, Path(dir), data_dir)
        except Exception as exc:
            logger.error("[ingest] 失败 %s: %s", f, exc)
            failed.append((f, str(exc)))
    if failed and total == 0:
        f, msg = failed[0]
        raise IngestError(f"全部 {len(files)} 个文件导入失败,首个: {f}: {msg}")
    if failed:
        logger.warning("[ingest] %d/%d 个文件失败:%s",
                       len(failed), len(files), ", ".join(str(f) for f, _ in failed))
    return total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m agents.kingdee_plugin_agent.tools.ingest",
        description="RAG 导入管线:URL/目录/内部 skill → api_ref|guide|experience 库"
                    "(代码感知分块,去重式幂等:内容变更需 --delete-source 删旧后重灌)",
    )
    ap.add_argument("--url", action="append", default=[], metavar="URL",
                    help="要导入的页面 URL(可重复,逐条失败继续)")
    ap.add_argument("--dir", type=Path, metavar="PATH",
                    help="批量导入目录下所有 *.md/*.cs(相对路径作 source)")
    ap.add_argument("--seed-internal", action="store_true",
                    help="导入内部 skill 文档(skills/**/*.md,SKILL.md + references)到 guide")
    ap.add_argument("--delete-source", metavar="SOURCE",
                    help="删除该 source 的全部条目后退出(--collection 指定库);编辑重灌前先删旧")
    ap.add_argument("--collection", required=True, choices=RAG_COLLECTIONS,
                    help="目标库: api_ref / guide / experience")
    ap.add_argument("--title", default="", help="URL 模式的标题(缺省从页面 <title>/<h1> 提取)")
    ap.add_argument("--data-dir", type=Path, help="数据目录(默认 data/kingdee-rag)")
    args = ap.parse_args(argv)

    try:
        if args.delete_source:
            n = delete_source(args.collection, args.delete_source, data_dir=args.data_dir)
            print(f"[ingest] 删除 {args.collection}/{args.delete_source}: {n} 条")
            return 0
        if args.seed_internal:
            if args.collection != "guide":
                print("[ingest] --seed-internal 仅支持 --collection guide", file=sys.stderr)
                return 2
            n = ingest_dir(_SKILLS_DIR, "guide", data_dir=args.data_dir)
            print(f"[ingest] 内部 skill 文档 → guide: +{n} chunks")
            return 0
        if args.dir:
            n = ingest_dir(args.dir, args.collection, data_dir=args.data_dir)
            print(f"[ingest] {args.dir} → {args.collection}: +{n} chunks")
            return 0
        if args.url:
            ok = 0
            for url in args.url:
                try:
                    n = ingest_url(url, args.collection, title=args.title, data_dir=args.data_dir)
                except IngestError as exc:
                    print(f"[ingest] 失败 {url}: {exc}", file=sys.stderr)
                    continue
                print(f"[ingest] {url}: +{n} chunks")
                ok += 1
            if ok == 0:
                print("[ingest] 全部 URL 导入失败", file=sys.stderr)
                return 1
            return 0
    except IngestError as exc:
        print(f"[ingest] 错误: {exc}", file=sys.stderr)
        return 1
    ap.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
