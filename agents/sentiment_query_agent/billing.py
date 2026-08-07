"""计费:创建 group 记 pending,commit 转正式计费。

设计见 docs/superpowers/specs/2026-08-06-sentiment-query-agent-sentiment-query-agent-design.md §8。

- 一次完整流程 = 1 计费单位。
- 创建 group:记 pending 记录;commit:转正式(committed)。
- 未 commit(失败/取消/过期)不计费。
- 防刷:同一用户 pending 记录限并发(默认 5)。
- **并发安全**:JSON 文件读-改-写非原子,并发会丢记录。
  用线程锁(进程内)+ fcntl 文件锁(跨进程)双保险,保证原子。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import HTTPException

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "billing"
_MAX_PENDING = 5  # 同一用户最多并发 pending 数
_PENDING_TTL = timedelta(hours=24)  # pending 超时自动视为放弃

# 进程内线程锁:同进程多协程/线程并发写计费文件时串行化
_thread_lock = threading.Lock()


def _user_path(user: str) -> Path:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _DATA_DIR / f"{user}.json"


def _load(user: str) -> list[dict]:
    p = _user_path(user)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _save(user: str, records: list[dict]) -> None:
    _user_path(user).write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _with_file_lock(user: str, fn):
    """持锁执行 fn:线程锁 + fcntl 文件锁(跨进程,如多 worker)。

    fcntl 仅 Unix;Windows 环境退化到仅线程锁。
    """
    with _thread_lock:
        try:
            import fcntl
        except ImportError:  # Windows
            return fn()
        lock_path = _user_path(user).with_suffix(".lock")
        with open(lock_path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def create_pending(user: str, group_id: str) -> None:
    """创建 group 时记 pending 计费记录(防刷:超并发拒绝)。并发安全。"""

    def _do() -> None:
        records = _load(user)
        active = [r for r in records if r["status"] == "pending"
                  and datetime.fromisoformat(r["created_at"]) > datetime.now() - _PENDING_TTL]
        if len(active) >= _MAX_PENDING:
            raise HTTPException(status_code=429, detail="并发 pending 超限,请先完成或取消未入库的方案组")
        records.append({
            "group_id": group_id,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "committed_at": None,
        })
        _save(user, records)

    _with_file_lock(user, _do)


def commit(user: str, group_id: str) -> None:
    """commit:转正式计费(1 单位)。并发安全。"""

    def _do() -> None:
        records = _load(user)
        for r in records:
            if r["group_id"] == group_id and r["status"] == "pending":
                r["status"] = "committed"
                r["committed_at"] = datetime.now().isoformat()
                _save(user, records)
                return
        raise HTTPException(status_code=404, detail="计费记录不存在(group 未创建或无 pending)")

    _with_file_lock(user, _do)
