"""交付包组装:源码 + DLL + 部署说明 + 设计/审查记录。"""
import json
import zipfile
from datetime import datetime
from pathlib import Path


class PackageBuilder:
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

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
