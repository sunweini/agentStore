"""w4 审查:规范库整库 + API 抽查。裁决契约: Approved | Needs fixes。

LLM 契约:ReviewOutput(findings),输入 = Plugin.cs + 规范库整库
(standards.inject_text)+ w4_review.md + 类型分支 prompt。裁决仍由确定性规则
_verdict_from_findings 计算(存在 Critical/Important → Needs fixes),LLM 只产 findings,
防 LLM 直接伪造裁决。

产物机制(终审 C7 裁决):Subtask 增加 review_path 字段,artifact_key = "review_path",
基类把 review.json 路径 setattr 进该字段;review_verdict 由 _execute 直接写入 ——
不再覆写 run(),基类 C3 契约原样保留。LLM 失败/llm=None → 确定性骨架 findings
(模板占位符残留 = Critical)。

未知插件类型(终审 C6):ERROR 上报而非裸 KeyError。
"""
import json
import re
from typing import Literal

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase

VERDICTS = ("Approved", "Needs fixes")
TYPE_PROMPTS = {"bill": "w4_review_bill.md", "service": "w4_review_service.md", "list": "w4_review_list.md"}
VALID_TYPES = tuple(TYPE_PROMPTS)

# 裁决规则(设计 §5.4):存在 Critical(必改)/Important(应改)→ Needs fixes(退回 w3);仅 Minor 或无误 → Approved
_BLOCKING_SEVERITIES = ("Critical", "Important")


def _verdict_from_findings(findings: list[dict]) -> str:
    blocking = [f for f in findings if f.get("severity") in _BLOCKING_SEVERITIES]
    return "Needs fixes" if blocking else "Approved"


class ReviewFinding(BaseModel):
    """单条审查意见(设计 §5.4 契约字段)。"""

    severity: Literal["Critical", "Important", "Minor"] = "Minor"
    line: int = 0
    issue: str = ""
    依据: str = ""
    修法: str = ""


class ReviewOutput(BaseModel):
    """w4 审查输出契约(经 with_structured_output 绑定)。"""

    findings: list[ReviewFinding] = Field(default_factory=list)


class ReviewWorker(WorkerBase):
    name = "w4"

    def __init__(self, llm, store, rag=None, standards=None):
        super().__init__(llm, store)
        self.rag = rag
        self.standards = standards

    def _detect_findings(self, code: str) -> list[dict]:
        """确定性骨架:模板占位符未渲染 → Critical(生成环节必须渲染全部 {{TOKEN}})。

        真实实现(C10):LLM 按规范库整库 + API 抽查产出 findings,
        每条含 severity/line/issue/依据/修法(设计 §5.4 契约)。
        """
        findings = []
        for lineno, line in enumerate(code.splitlines(), start=1):
            for token in sorted(set(re.findall(r"\{\{\w+\}\}", line))):
                findings.append({
                    "severity": "Critical",
                    "line": lineno,
                    "issue": f"模板占位符 {token} 未渲染",
                    "依据": "w3 生成契约:全部 {{TOKEN}} 必须填值,冲突以模板为准",
                    "修法": "补齐该占位符对应的业务实现,或删除模板残留",
                })
        return findings

    def _llm_review(self, state, subtask, code: str, rules: str, prompt: str):
        """LLM 审查 → findings 列表(ReviewFinding);失败返回 None → 骨架。"""
        if self.llm is None:
            return None
        try:
            context = json.dumps({
                "code": code,
                "standards": rules,
                "plugin_type": subtask.plugin_type,
                "title": subtask.title,
            }, ensure_ascii=False)
            prompt = ChatPromptTemplate.from_messages([
                ("system", prompt),
                ("human", "代码与规范:\n{context}"),  # JSON 走占位符,防 f-string 花括号冲突(dev-standards §7.2)
            ])
            out = self.llm.with_structured_output(ReviewOutput).invoke(
                prompt.format_messages(context=context))
            return out.findings if out else None
        except Exception:
            return None  # LLM 故障 → 骨架

    def _execute(self, state, subtask) -> dict:
        try:
            code = self.store.read(subtask.id, "Plugin.cs")
        except Exception as e:
            return {"status": "ERROR", "artifact_key": "", "evidence": "",
                    "concerns": f"代码产物缺失: {e},子任务 {subtask.id} 标记失败"}
        branch = TYPE_PROMPTS.get(subtask.plugin_type)
        if branch is None:
            return {"status": "ERROR", "artifact_key": "", "evidence": "",
                    "concerns": f"未知插件类型 {subtask.plugin_type!r}(合法: {VALID_TYPES}),子任务 {subtask.id} 标记失败"}
        rules = self.standards.inject_text() if self.standards else ""
        prompt = self._load_prompt("w4_review.md") + "\n" + self._load_prompt(branch)
        findings = self._llm_review(state, subtask, code, rules, prompt)
        if findings is None:
            findings = self._detect_findings(code)
        normalized = [f.model_dump() if hasattr(f, "model_dump") else f for f in findings]
        verdict = _verdict_from_findings(normalized)
        subtask.review_verdict = verdict
        path = self.store.write(subtask.id, "review.json",
                                json.dumps(normalized, ensure_ascii=False))
        return {"status": "DONE", "artifact_key": "review_path", "review_verdict": verdict,
                "path": str(path), "evidence": f"{verdict}, {len(normalized)} findings",
                "concerns": ""}
