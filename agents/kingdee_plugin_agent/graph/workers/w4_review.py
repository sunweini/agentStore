"""w4 审查:规范库整库 + API 抽查。裁决契约: Approved | Needs fixes。

骨架路径:读 Plugin.cs → 规范库整库注入(standards.inject_text)→ 确定性
findings(模板占位符残留 = Critical)→ 按裁决规则定 verdict → 落盘 review.json
→ 写回 subtask.review_verdict。

⚠️ run() 覆写:基类 C3 契约把 artifact 路径 setattr 到 artifact_key 指名的
State 字段(review_verdict),但 review_verdict 是裁决值而非路径;基类 setattr
会把裁决值冲成 review.json 路径。故在基类上报后从 report 还原裁决值(基类
不改动,影响面收敛在本 worker)。
"""
import json
import re

from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase

VERDICTS = ("Approved", "Needs fixes")
TYPE_PROMPTS = {"bill": "w4_review_bill.md", "service": "w4_review_service.md", "list": "w4_review_list.md"}

# 裁决规则(设计 §5.4):存在 Critical(必改)/Important(应改)→ Needs fixes(退回 w3);仅 Minor 或无误 → Approved
_BLOCKING_SEVERITIES = ("Critical", "Important")


def _verdict_from_findings(findings: list[dict]) -> str:
    blocking = [f for f in findings if f.get("severity") in _BLOCKING_SEVERITIES]
    return "Needs fixes" if blocking else "Approved"


class ReviewWorker(WorkerBase):
    name = "w4"

    def __init__(self, llm, store, rag=None, standards=None):
        super().__init__(llm, store)
        self.rag = rag
        self.standards = standards

    def run(self, state, subtask):
        """基类 run() 会把 review.json 路径 setattr 进 review_verdict(C3 契约:
        artifact_key→State 字段),此处还原 _execute 已定的裁决值(report 携带)。"""
        subtask, msg = super().run(state, subtask)
        subtask.review_verdict = subtask.report.get("review_verdict", "")
        return subtask, msg

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

    def _execute(self, state, subtask) -> dict:
        code = self.store.read(subtask.id, "Plugin.cs")
        rules = self.standards.inject_text() if self.standards else ""
        base = self._load_prompt("w4_review.md")
        branch = self._load_prompt(TYPE_PROMPTS[subtask.plugin_type])
        # 真实实现:LLM 输入 code + 规范库整库(rules) + 类型要点(base/branch)
        #   + API 抽查 → findings(Critical/Important/Minor 列表)
        findings = self._detect_findings(code)
        verdict = _verdict_from_findings(findings)
        subtask.review_verdict = verdict
        path = self.store.write(subtask.id, "review.json", json.dumps(findings, ensure_ascii=False))
        return {"status": "DONE", "artifact_key": "review_verdict", "review_verdict": verdict,
                "path": str(path), "evidence": f"{verdict}, {len(findings)} findings",
                "concerns": ""}
