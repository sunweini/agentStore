"""数据模型:agent1 舆情方案生成 Agent 的图状态。

设计见 docs/superpowers/specs/2026-08-06-agent1-sentiment-query-agent-design.md §5。

层级:SchemeGroup(方案组) → Scheme(方案) → Track(轨)。
勾选:方案级 selected + 轨级 selected 两级;汇总 = 勾选轨数 = 任务行数。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

# 轨类型固定 6 类(与 skill references/output-formats.md 对齐)
TRACK_KEYS = ("a", "b", "c", "快讯", "司法", "招标")

# 方案状态
STATUS_GENERATING = "generating"   # 生成中
STATUS_REVIEW = "review"           # 待勾选
STATUS_COMMITTED = "committed"     # 已入库(冻结)


class Track(TypedDict, total=False):
    """检索轨:一组检索式 = 一个任务行。"""

    key: str                 # a/b/c/快讯/司法/招标
    boolean_query: str       # 布尔语法检索式
    google_query: str        # Google 语法检索式
    sources: list[str]       # 属地信源白名单(域名)
    frequency: str           # 快讯/小时级/日级/周级/双周/月级
    risk: str                # critical/high/medium/low
    relevance: str           # direct/indirect/context
    selected: bool           # 勾选状态


class Scheme(TypedDict, total=False):
    """方案:方案组内一个检索维度(Q0 集团层/Q1 国别项目群…)。"""

    id: str                  # Q0/Q1…
    name: str
    region: str              # 全语种/国家/区域
    lang: str                # 中/英/法…
    desc: str
    gaps: list[str]          # GAP 标注
    tracks: list[Track]
    selected: bool           # 方案级勾选


class SchemeGroup(TypedDict, total=False):
    """方案组:一个监控主体 = 一个方案组。"""

    group_id: str            # = LangGraph thread_id
    owner: str               # apikey 标识的用户
    company_name: str        # 中文公司名
    meta: dict               # 主体角色/相关度口径/重点地区/检索类型
    status: str              # STATUS_*
    step_status: list        # 6 步状态:[{step, status, output}]
    profile: dict            # 步骤 2 产物:主体画像
    entities: dict           # 步骤 1 产物:实体测绘
    keywords: list           # 步骤 3 产物:关键词字典
    schemes: list[Scheme]    # 步骤 4+5+6 产物
    created_at: str
    committed_at: str | None


class AgentState(TypedDict):
    """LangGraph 图状态。

    - messages: 各步 LLM 对话/工具调用历史(复用 add_messages 自动追加)。
    - group: 方案组数据,逐步累积。
    - current_step: 当前执行步骤(1-6),进度展示用。
    """

    messages: Annotated[list, add_messages]
    group: SchemeGroup
    current_step: int
