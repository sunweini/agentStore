"""w5.5 部署冒烟:运行时验证(assembly 加载 + FormId 映射)。

防"编译过跑不起来":编译通过 ≠ 能跑。deploy_and_verify 失败(assembly 未
加载/FormId 映射错)→ 扣全局返工预算 rework_budget_left 后 BLOCKED(退回
w5/w3);成功 → DONE。form_id 取自 state.environment(默认空串)。

终审 C8 修复:SmokeClient.deploy_and_verify 契约参数是 Path,这里显式传
Path(subtask.code_path)(不再传裸 str);客户端未配置(KD_BASE_URL 缺失)→
BLOCKED 但**不扣预算** —— 基础设施缺失走重工无意义,由图上包装器按
"未扣预算的 BLOCKED = 基础设施故障"标记子任务 failed,防无限重试循环。
"""
from pathlib import Path

from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase


class SmokeWorker(WorkerBase):
    name = "w5_5"

    def __init__(self, llm, store, smoke_client):
        super().__init__(llm, store)
        self.smoke = smoke_client

    def _execute(self, state, subtask) -> dict:
        if self.smoke is None:
            return {"status": "BLOCKED", "artifact_key": "", "evidence": "",
                    "concerns": "冒烟客户端未配置(KD_BASE_URL 缺失),子任务标记失败"}
        r = self.smoke.deploy_and_verify(Path(subtask.code_path or ""),
                                         state.environment.get("form_id", ""))
        if not r.ok:
            state.rework_budget_left -= 1
            return {"status": "BLOCKED", "artifact_key": "", "evidence": r.detail,
                    "concerns": "冒烟失败,退回 w5/w3"}
        return {"status": "DONE", "artifact_key": "", "evidence": r.detail,
                "concerns": ""}
