"""JSON 文件库:方案组/草稿/索引 读写。

设计见 docs/superpowers/specs/2026-08-06-agent1-sentiment-query-agent-design.md §7。

- 草稿:data/schemes/<group_id>.draft.json(生成中/待勾选)
- 正式:data/schemes/<group_id>.json(commit 后,冻结)
- 索引:data/schemes/index.json(方案组列表)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "schemes"


def _ensure_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


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
    """更新索引文件(方案组列表页用)。"""
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


def load_group(group_id: str) -> dict | None:
    """读正式文件;无则读草稿;都无返回 None。"""
    for suffix in ("json", "draft.json"):
        p = _DATA_DIR / f"{group_id}.{suffix}"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def list_groups(owner: str) -> list[dict]:
    """按 owner 列方案组索引。"""
    index_path = _DATA_DIR / "index.json"
    if not index_path.exists():
        return []
    index = json.loads(index_path.read_text(encoding="utf-8"))
    return [e for e in index if e.get("owner") == owner]
