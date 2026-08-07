#!/usr/bin/env python3
"""分步脚本共享库:格式校验/标准化/GAP 记录。

每个 stepN.py 读 stdin JSON(LLM 原始输出),按本文件契约校验标准化,
输出 stdout JSON。缺字段记 GAP(编号 GAP00N)。
"""

from __future__ import annotations

import json
import sys
from typing import Any

TRACK_KEYS = ("a", "b", "c", "快讯", "司法", "招标")
FREQUENCIES = ("快讯/小时级", "日级", "周级", "双周级", "月级")
RISKS = ("critical", "high", "medium", "low")
RELEVANCES = ("direct", "indirect", "context")
LAYERS = ("A", "B", "C", "D", "R", "X")

_gaps: list[str] = []


def fail(msg: str) -> None:
    """格式错误 → 非 0 退出 + stderr(节点据此重试)。"""
    sys.stderr.write(f"FORMAT_ERROR: {msg}\n")
    sys.exit(1)


def load_input() -> dict:
    """读 stdin JSON。非 JSON → fail。"""
    try:
        raw = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        fail(f"LLM 输出非 JSON: {e}")
    if not isinstance(raw, dict):
        fail("LLM 输出必须是 JSON 对象")
    return raw


def emit(data: dict) -> None:
    """输出标准化 JSON。"""
    print(json.dumps(data, ensure_ascii=False, indent=2))


def gap(fmt: str, *args: Any) -> None:
    """记 GAP(编号 GAP00N)。"""
    _gaps.append(f"GAP{len(_gaps) + 1:03d} " + (fmt % args if args else fmt))


def with_gaps(data: dict) -> dict:
    """合并 GAP 到输出(挂 _gaps 字段,供上层层层传递)。"""
    if _gaps:
        data["_gaps"] = _gaps
    return data


def norm_str(v: Any, field: str, default: str = "", required: bool = False) -> str:
    """标准化字符串字段。缺失/非字符串:required → fail,否则默认 + GAP。"""
    if isinstance(v, str) and v.strip():
        return v.strip()
    if required:
        fail(f"字段 {field} 缺失或非字符串")
    gap(f"字段 {field} 缺失,补默认 {default!r}")
    return default


def norm_list(v: Any, field: str, required: bool = False) -> list:
    """标准化列表字段。"""
    if isinstance(v, list):
        return v
    if required:
        fail(f"字段 {field} 缺失或非列表")
    gap(f"字段 {field} 缺失,补空列表")
    return []


def norm_choice(v: Any, field: str, choices: tuple, default: str, required: bool = False) -> str:
    """标准化枚举字段。非法值:required → fail,否则默认 + GAP。"""
    if isinstance(v, str) and v in choices:
        return v
    if required:
        fail(f"字段 {field} 值 {v!r} 非法,允许: {choices}")
    gap(f"字段 {field} 值 {v!r} 非法,补默认 {default!r}")
    return default
