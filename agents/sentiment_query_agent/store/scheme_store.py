"""JSON 文件库:方案组/草稿/索引 读写。

设计见 docs/superpowers/specs/2026-08-06-sentiment-query-agent-sentiment-query-agent-design.md §7。

- 草稿:data/schemes/<group_id>.draft.json(生成中/待勾选)
- 正式:data/schemes/<group_id>.json(commit 后,冻结)
- 索引:data/schemes/index.json(方案组列表)

并发安全:index.json 读-改-写非原子,用线程锁(进程内)+ fcntl 文件锁
(跨进程,为多 worker 预留)双保险,模式对齐 billing.py。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "schemes"

# 进程内线程锁:同进程多协程/线程并发读写索引时串行化
_index_lock = threading.Lock()


def _ensure_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


def _with_index_lock(fn):
    """持锁执行 fn:线程锁 + fcntl 文件锁(跨进程,如多 worker)。

    fcntl 仅 Unix;Windows 环境退化到仅线程锁。
    """
    with _index_lock:
        try:
            import fcntl
        except ImportError:  # Windows
            return fn()
        _ensure_dir()
        lock_path = _DATA_DIR / "index.lock"
        with open(lock_path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def save_draft(group: dict) -> None:
    """存草稿(未 commit)。"""
    _ensure_dir()
    (_DATA_DIR / f"{group['group_id']}.draft.json").write_text(
        json.dumps(group, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def save_committed(group: dict) -> None:
    """commit:写正式文件(冻结),删草稿,更新索引。"""
    _ensure_dir()
    group["committed_at"] = datetime.now().isoformat()
    (_DATA_DIR / f"{group['group_id']}.json").write_text(
        json.dumps(group, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    draft = _DATA_DIR / f"{group['group_id']}.draft.json"
    if draft.exists():
        draft.unlink()
    _update_index(group)


def _update_index(group: dict) -> None:
    """更新索引文件(方案组列表页用)。并发安全。"""

    def _do() -> None:
        index_path = _DATA_DIR / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
        entry = {
            "group_id": group["group_id"],
            "company_name": group["company_name"],
            "status": group["status"],
            "owner": group["owner"],
            "created_at": group.get("created_at", ""),
            "committed_at": group.get("committed_at"),
        }
        index = [e for e in index if e["group_id"] != group["group_id"]] + [entry]
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    _with_index_lock(_do)


def load_group(group_id: str) -> dict | None:
    """读正式文件;无则读草稿;都无返回 None。"""
    for suffix in ("json", "draft.json"):
        p = _DATA_DIR / f"{group_id}.{suffix}"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def list_groups(owner: str) -> list[dict]:
    """按 owner 列方案组索引。持锁读,避免读到写一半的索引。"""

    def _do() -> list[dict]:
        index_path = _DATA_DIR / "index.json"
        if not index_path.exists():
            return []
        index = json.loads(index_path.read_text(encoding="utf-8"))
        return [e for e in index if e.get("owner") == owner]

    return _with_index_lock(_do)
