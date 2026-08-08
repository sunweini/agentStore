"""产物落盘:State 只存引用+摘要,细节走文件路径(主管上下文保护)。"""
from pathlib import Path


class ArtifactStoreError(RuntimeError):
    pass


class ArtifactStore:
    def __init__(self, root: Path = Path("data/kingdee-artifacts")):
        self.root = Path(root)

    def _sub_dir(self, subtask_id: str) -> Path:
        d = self.root / subtask_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write(self, subtask_id: str, name: str, content: str) -> Path:
        p = self._sub_dir(subtask_id) / name
        p.write_text(content, encoding="utf-8")
        return p

    def read(self, subtask_id: str, name: str) -> str:
        p = self._sub_dir(subtask_id) / name
        if not p.exists():
            raise ArtifactStoreError(f"产物不存在: {p}")
        return p.read_text(encoding="utf-8")

    def paths(self, subtask_id: str) -> dict[str, Path]:
        """该子任务已落盘的产物:name -> 路径(接口契约 C1,下游 worker 依赖)。"""
        d = self._sub_dir(subtask_id)
        return {p.name: p for p in d.iterdir() if p.is_file()}
