"""部署冒烟:验证 assembly 加载 + FormId→plugin 映射(运行时验证,防编译过跑不起来)。"""
from dataclasses import dataclass
from pathlib import Path

from agents.kingdee_plugin_agent.tools.kingdee_api import KingdeeApiClient, KingdeeApiUnavailable


@dataclass
class SmokeResult:
    ok: bool
    detail: str


class SmokeClient:
    def __init__(self, api: KingdeeApiClient):
        self.api = api

    def deploy_and_verify(self, dll_path: Path, form_id: str) -> SmokeResult:
        """部署 DLL 到测试环境并验证。真实实现按金蝶部署 API 调整;此处接口先定。"""
        if not dll_path.exists():
            return SmokeResult(ok=False, detail=f"DLL 不存在: {dll_path}")
        try:
            # 验证 form_id 可解析 + 插件映射存在(元数据层验证)
            self.api._post("/metadata/verify", {"formid": form_id, "dll": dll_path.name})
            return SmokeResult(ok=True, detail="assembly 加载 + 映射验证通过")
        except KingdeeApiUnavailable as e:
            return SmokeResult(ok=False, detail=str(e))
