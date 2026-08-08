"""w5.5 部署冒烟:运行时验证(assembly 加载 + FormId 映射)。

防"编译过跑不起来":编译通过 ≠ 能跑。deploy_and_verify 失败(assembly 未
加载/FormId 映射错)→ 扣全局返工预算 rework_budget_left 后 BLOCKED(退回
w5/w3);成功 → DONE。form_id 取自 state.environment(默认空串)。

DLL 链路(结构级修复):冒烟验证对象是 **编译产物 DLL**(subtask.dll_path,
w5 成功时取自编译后端),不再误用源码 Plugin.cs(code_path)。编译后端未产出
DLL(mock 后端)→ 跳过部署验证:DONE_WITH_CONCERNS 显式标注"无 DLL",不扣
预算、不计冒烟指标 —— 跳过不是冒烟结果;接真实 msbuild 后端后自动恢复验证。

终审 C8 修复:SmokeClient.deploy_and_verify 契约参数是 Path,这里显式传
Path(subtask.dll_path)(不再传裸 str);客户端未配置(KD_BASE_URL 缺失)→
BLOCKED 但**不扣预算** —— 基础设施缺失走重工无意义,由图上包装器按
"未扣预算的 BLOCKED = 基础设施故障"标记子任务 failed,防无限重试循环。
"""
from pathlib import Path

from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase

#: 跳过部署验证的明确原因(结构级修复:mock 编译后端无 DLL 产出)
_SKIP_DETAIL = "无 DLL(编译后端未产出),跳过部署验证"


class SmokeWorker(WorkerBase):
    name = "w5_5"

    def __init__(self, llm, store, smoke_client):
        super().__init__(llm, store)
        self.smoke = smoke_client

    def _execute(self, state, subtask) -> dict:
        if self.smoke is None:
            return {"status": "BLOCKED", "artifact_key": "", "evidence": "",
                    "concerns": "冒烟客户端未配置(KD_BASE_URL 缺失),子任务标记失败"}
        if not subtask.dll_path:
            # 无 DLL(编译后端未产出):跳过部署验证,不扣预算不计指标 ——
            # 显式标注跳过原因,不再拿源码 Plugin.cs 冒充 DLL 去验证。
            return {"status": "DONE_WITH_CONCERNS", "artifact_key": "",
                    "evidence": _SKIP_DETAIL, "concerns": _SKIP_DETAIL}
        r = self.smoke.deploy_and_verify(Path(subtask.dll_path),
                                         state.environment.get("form_id", ""))
        if not r.ok:
            state.metrics["smoke_fail_count"] += 1   # 指标:冒烟失败(设计 §12)
            state.rework_budget_left -= 1
            return {"status": "BLOCKED", "artifact_key": "", "evidence": r.detail,
                    "concerns": "冒烟失败,退回 w5/w3"}
        state.metrics["smoke_pass_count"] += 1       # 指标:冒烟通过(设计 §12)
        return {"status": "DONE", "artifact_key": "", "evidence": r.detail,
                "concerns": ""}
