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
        out = self.output_dir / f"deliverable-{ts}.zip"
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("source/Plugin.cs", deliverable.get("code", ""))
            dll = deliverable.get("dll_path")
            if dll and Path(dll).exists():
                z.write(dll, "bin/Plugin.dll")
            z.writestr("deploy.md", "部署说明:上传 bin/Plugin.dll 到金蝶 BOS 插件目录,刷新注册\n")
            z.writestr("records/design.json", json.dumps(deliverable.get("design", {}), ensure_ascii=False, indent=2))
            z.writestr("records/review.json", json.dumps(deliverable.get("review", {}), ensure_ascii=False, indent=2))
        return out
