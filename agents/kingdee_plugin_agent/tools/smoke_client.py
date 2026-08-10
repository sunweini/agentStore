"""部署冒烟:验证 DLL 产物 + FormId 目标单据(运行时验证,防编译过跑不起来)。

金蝶 WebAPI 无 assembly 加载验证接口,真实冒烟分两层:
1. **DLL 产物校验**(本地):存在性 + PE 头(真实 DLL 以 MZ 开头,mock 桩不符合)
   —— 防"编译通过但产物是空壳/源码冒充"。
2. **FormId 目标校验**(WebAPI):QueryBusinessInfo 验证 form_id 是真实单据
   —— 插件挂载目标存在,防"部署到不存在的单据"。
assembly 加载/映射生效属部署后行为,由用户验收 + 反馈端点
(POST /tasks/{id}/feedback)人工确认,本客户端不冒充验证。

⚠️ 2026-08-10 真实实例联调后:原占位端点 /metadata/verify 移除
(官方 SDK 无此接口),校验改为上述两层;DLL 需人工部署到金蝶服务器
WebSite\bin 后重启站点才生效 —— 本客户端负责"产物合格 + 目标真实",
不负责文件部署(无 WebAPI 通道)。
"""
from dataclasses import dataclass
from pathlib import Path

from agents.kingdee_plugin_agent.tools.kingdee_api import KingdeeApiClient, KingdeeApiUnavailable


@dataclass
class SmokeResult:
    ok: bool
    detail: str


class SmokeClient:
    def __init__(self, api: KingdeeApiClient | None):
        self.api = api

    def deploy_and_verify(self, dll_path: Path, form_id: str) -> SmokeResult:
        """校验 DLL 产物 + FormId 目标真实存在。

        失败返回 ok=False,detail 指明原因;API 不可用(登录失败/网络)→
        ok=False(冒烟失败走 BLOCKED,不伪造成功)。
        """
        if not dll_path.exists():
            return SmokeResult(ok=False, detail=f"DLL 不存在: {dll_path}")
        if not _is_pe_dll(dll_path):
            return SmokeResult(ok=False, detail=f"DLL 非有效 PE 文件(可能源码/空壳冒充): {dll_path}")
        if self.api is None:
            return SmokeResult(ok=False, detail="冒烟客户端未配置(KD_BASE_URL 缺失)")
        if not form_id:
            return SmokeResult(ok=False, detail="form_id 为空(需求澄清未提取目标单据)")
        try:
            # QueryBusinessInfo 验证 form_id 真实存在(返回字段数 > 0)
            fields = self.api.get_form_fields(form_id)
            if not fields:
                return SmokeResult(ok=False, detail=f"FormId 目标单据无字段元数据: {form_id}")
            return SmokeResult(ok=True, detail=f"产物合格(PE DLL)+ FormId 真实({len(fields)} 字段)")
        except KingdeeApiUnavailable as e:
            return SmokeResult(ok=False, detail=str(e))


def _is_pe_dll(path: Path) -> bool:
    """真实 DLL 以 PE 头 MZ 开头;mock 桩(b"mock-dll" 等)非 PE,拒绝。"""
    try:
        with path.open("rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False
