"""交付包组装:源码 + DLL + 部署说明 + 设计/审查记录 + 失败收尾"未完成"包。"""
import json
import zipfile
from datetime import datetime
from pathlib import Path


class PackageBuilder:
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build_failed(self, subtasks: list[dict], reason: str, spec_version: int = 1,
                     requirement_spec: dict | None = None) -> Path:
        """失败收尾"未完成"包(设计 §8):部分产物 + 全部退回意见 + 失败原因。

        文件名 `deliverable-failed-<ts>.zip` 明确标注失败态,与正常交付包区分;
        records/status.json 记录原因(status= failed / reason / spec_version +
        冻结 spec 快照);每个未交付子任务一个目录 `subtasks/<sid>/`,内含已有
        产物 —— source/Plugin.cs(代码,有则收)、design.md(设计)、
        review.json(审查记录,含 Minor 全部意见)、compile_errors.json
        (编译超限 5 轮后的错误日志)、status.txt(子任务状态 + 审查裁决)。
        产物缺失(子任务未走到该阶段)自然跳过,不阻塞打包。

        Args:
            subtasks: 每个未交付子任务的产物字典,键:id/status/code/design/
                review/compile_errors/review_verdict(缺失容忍)。
            reason: 失败原因(state.action,如 "fail:返工预算耗尽")。
            spec_version: 需求版本号(确认即冻结的盖章值)。
            requirement_spec: 冻结的需求 spec 快照(可审计对应哪版需求)。

        Returns:
            未完成包路径。
        """
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = self.output_dir / f"deliverable-failed-{ts}.zip"
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("records/status.json", json.dumps({
                "status": "failed",
                "reason": reason,
                "spec_version": spec_version,
                "requirement_spec": requirement_spec or {},
            }, ensure_ascii=False, indent=2))
            for entry in subtasks:
                sid = entry.get("id", "unknown")
                base = f"subtasks/{sid}"
                if entry.get("code"):
                    z.writestr(f"{base}/source/Plugin.cs", entry["code"])
                if entry.get("design"):
                    z.writestr(f"{base}/design.md", entry["design"])
                if entry.get("review"):
                    z.writestr(f"{base}/review.json",
                               json.dumps(entry["review"], ensure_ascii=False, indent=2))
                if entry.get("compile_errors"):
                    z.writestr(f"{base}/compile_errors.json",
                               json.dumps(entry["compile_errors"], ensure_ascii=False,
                                          indent=2))
                z.writestr(f"{base}/status.txt",
                           f"status: {entry.get('status', '')}\n"
                           f"review_verdict: {entry.get('review_verdict', '')}\n")
        return out

    def build(self, deliverable: dict) -> Path:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        # 文件名带子任务 id:多子任务并行打包各得各的包,互不覆盖(C9 合并契约 v1)
        sid = deliverable.get("subtask_id", "all")
        out = self.output_dir / f"deliverable-{sid}-{ts}.zip"
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("source/Plugin.cs", deliverable.get("code", ""))
            dll = deliverable.get("dll_path")
            if dll and Path(dll).exists():
                z.write(dll, "bin/Plugin.dll")
            z.writestr("deploy.md", "部署说明:上传 bin/Plugin.dll 到金蝶 BOS 插件目录,刷新注册\n")
            z.writestr("records/design.json", json.dumps(deliverable.get("design", {}), ensure_ascii=False, indent=2))
            z.writestr("records/review.json", json.dumps(deliverable.get("review", {}), ensure_ascii=False, indent=2))
            # 需求版本冻结记录:spec_version + 冻结的需求 spec 快照(设计 §8)。
            # 交付物可审计"这份包对应哪版需求";无 spec_version 时给 1(兼容直连调用)。
            z.writestr("records/spec.json", json.dumps({
                "spec_version": deliverable.get("spec_version", 1),
                "requirement_spec": deliverable.get("requirement_spec", {}),
            }, ensure_ascii=False, indent=2))
        return out
