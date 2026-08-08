"""w5 编译修复:健康探测 → 提交 → 错误 → 经验库检索修复 → 重编(上限 5)。

流程(设计 §6.6):
  1. 编译前先 health() 探测:服务不可用(容器未起)→ BLOCKED,**不计编译轮次**。
  2. 循环编译至多 MAX_COMPILE_ROUNDS 轮:成功 → compile_errors 清空 + DONE;
     失败 → 错误记入 subtask.compile_errors,按错误码检索经验库(ExperienceStore)
     取修复建议附注,供 C10 LLM 改写代码后写回重编。
  3. 5 轮仍失败 → 扣全局返工预算 rework_budget_left 后 BLOCKED(退回 w3/w4 或问用户)。

C10 契约(LLM 修复接线):_retrieve_fix 只做经验检索附注;C10 依 w5_compile.md
prompt + compile_errors(含 experience 附注)改写代码,并
store.write(subtask.id, "Plugin.cs", new_code) 写回。
"""
from compile_service.models import CompileUnavailableError

from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase

MAX_COMPILE_ROUNDS = 5


class CompileWorker(WorkerBase):
    name = "w5"

    def __init__(self, llm, store, compile_client, experience=None):
        super().__init__(llm, store)
        self.client = compile_client
        self.experience = experience

    def _retrieve_fix(self, subtask, errors) -> None:
        """修复循环骨架:按编译错误检索经验库,命中附注到 compile_errors 条目。

        C10 契约:LLM 依据 compile_errors(含 experience 附注)改写代码,并
        store.write(subtask.id, "Plugin.cs", new_code) 写回;本骨架只做检索
        附注,不改代码(占位,接口先定)。
        """
        if self.experience is None:
            return
        for entry, err in zip(subtask.compile_errors, errors):
            hits = self.experience.search_related(err.code, err.message, k=2)
            if hits:
                entry["experience"] = [h["text"] for h in hits]

    def _execute(self, state, subtask) -> dict:
        if not self.client.health():
            return {"status": "BLOCKED", "artifact_key": "", "evidence": "",
                    "concerns": "编译服务不可用(容器未起),不计编译轮次"}
        code = self.store.read(subtask.id, "Plugin.cs")
        for i in range(MAX_COMPILE_ROUNDS):
            try:
                result = self.client.compile(code, subtask.id)
            except CompileUnavailableError:
                return {"status": "BLOCKED", "artifact_key": "", "evidence": "",
                        "concerns": "编译服务 503"}
            if result.success:
                subtask.compile_errors = []
                return {"status": "DONE", "artifact_key": "code_path",
                        "path": subtask.code_path, "evidence": f"编译通过(第 {i+1} 轮)",
                        "concerns": ""}
            subtask.compile_errors = [{"code": e.code, "message": e.message}
                                      for e in result.errors]
            self._retrieve_fix(subtask, result.errors)
            # C10:LLM 按 w5_compile.md 改写代码后 store.write 写回重编
        state.rework_budget_left -= 1
        return {"status": "BLOCKED", "artifact_key": "", "evidence": "编译 5 轮失败",
                "concerns": "编译超限,退回 w3/w4 或问用户"}
