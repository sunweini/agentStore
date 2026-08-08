"""w5 编译修复:健康探测 → 提交 → 错误 → 经验检索 → LLM 改写代码 → 重编(上限 5)。

流程(设计 §6.6):
  1. 编译前先 health() 探测:服务不可用(容器未起)→ BLOCKED,**不计编译轮次**。
  2. 循环编译至多 MAX_COMPILE_ROUNDS 轮:成功 → compile_errors 清空 + DONE;
     失败 → 错误记入 subtask.compile_errors,按错误码检索经验库(ExperienceStore)
     取修复建议附注,再让 LLM 依 w5_compile.md prompt + 错误(含 experience 附注)
     改写代码后 store.write 写回重编(终审 C8:必须真实改写,禁止原样重提交)。
  3. 5 轮仍失败 → 扣全局返工预算 rework_budget_left 后 BLOCKED(退回 w3/w4 或问用户)。

经验库故障(终审 C8):_retrieve_fix 整体 try/except,不阻断编译循环。
"""
import json

from langchain_core.prompts import ChatPromptTemplate
from compile_service.models import CompileUnavailableError

from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase
from agents.kingdee_plugin_agent.graph.workers.w3_generate import CodeOutput

MAX_COMPILE_ROUNDS = 5


class CompileWorker(WorkerBase):
    name = "w5"

    def __init__(self, llm, store, compile_client, experience=None):
        super().__init__(llm, store)
        self.client = compile_client
        self.experience = experience

    def _retrieve_fix(self, subtask, errors) -> None:
        """按编译错误检索经验库,命中附注到 compile_errors 条目。

        只做检索附注(不改代码);改写由 _llm_fix 完成。经验库故障
        (chroma 不可用等)不阻断编译循环。
        """
        if self.experience is None:
            return
        try:
            for entry, err in zip(subtask.compile_errors, errors):
                hits = self.experience.search_related(err.code, err.message, k=2)
                if hits:
                    entry["experience"] = [h["text"] for h in hits]
        except Exception:
            pass  # 经验库故障 → 无附注继续,不阻断

    def _llm_fix(self, subtask, code: str) -> str | None:
        """LLM 依 w5_compile.md + compile_errors(含 experience 附注)改写代码。

        返回改写后的完整代码;未改写/LLM 故障返回 None(上层原样重提交,受轮次上限约束)。
        """
        if self.llm is None:
            return None
        try:
            prompt = self._load_prompt("w5_compile.md")
            context = json.dumps({"code": code, "compile_errors": subtask.compile_errors},
                                 ensure_ascii=False)
            prompt = ChatPromptTemplate.from_messages([
                ("system", prompt),
                ("human", "当前代码与错误列表:\n{context}"),  # JSON 走占位符,防 f-string 花括号冲突(dev-standards §7.2)
            ])
            out = self.llm.with_structured_output(CodeOutput).invoke(
                prompt.format_messages(context=context))
            new = out.code.strip() if out else ""
            return new if new and new != code else None  # 防原样重提交
        except Exception:
            return None  # LLM 故障 → 重编译同码(轮次上限兜底)

    def _execute(self, state, subtask) -> dict:
        if self.client is None:
            return {"status": "BLOCKED", "artifact_key": "", "evidence": "",
                    "concerns": "编译客户端未配置(COMPILE_SERVICE_URL 缺失),子任务标记失败"}
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
                        "path": subtask.code_path, "evidence": f"编译通过(第 {i + 1} 轮)",
                        "concerns": ""}
            subtask.compile_errors = [{"code": e.code, "message": e.message}
                                      for e in result.errors]
            self._retrieve_fix(subtask, result.errors)
            fixed = self._llm_fix(subtask, code)
            if fixed is not None:
                code = fixed
                self.store.write(subtask.id, "Plugin.cs", code)  # 改写后写回重编
        state.rework_budget_left -= 1
        return {"status": "BLOCKED", "artifact_key": "", "evidence": "编译 5 轮失败",
                "concerns": "编译超限,退回 w3/w4 或问用户"}
