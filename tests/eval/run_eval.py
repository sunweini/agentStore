"""生成质量 eval:case 跑 w3 生成 + mock 编译 + 事件断言,输出通过率(基线记录)。

用法:
  - 确定性基线(提交/CI):llm=None + default_mock_compiler() → 固定结果,
    已记录 baseline.json;prompt/模板变更后重跑对比(回归检测)。
  - 真实 LLM 评估(手动):传真实 llm 实例(worker 走 with_structured_output
    生成)+ 真实编译客户端(CompileClient/msbuild 后端),人工核对报告。

评估维度:
  1. compiled:mock 编译通过(EVAL_MOCK_RULES 拦截未渲染 {{TOKEN}} / 空代码;
     真实编译质量门在 w5 msbuild)。
  2. trigger_ok:case 的 expected_trigger 必须作为 override 方法声明出现在
     生成代码里(裸子串会命中设计摘要注释,误报)。mock 编译对事件正确性
     零信号(挂错事件照样编译通过),事件断言才是 w3 生成正确性的回归指标。
  MockCompiler 默认规则模拟"缺引用"型错误(出现 Kingdee.BOS /
  AbstractOperationServicePlugIn 字面量即命中),真实生成代码必然含这些字面量,
  属误报,评估不用;评估规则见 EVAL_MOCK_RULES。
"""
import json
import tempfile
from pathlib import Path

from compile_service.backends.mock import MockCompiler
from compile_service.models import CompileFile

from agents.kingdee_plugin_agent.graph.state import Subtask, TaskState
from agents.kingdee_plugin_agent.graph.workers.w3_generate import GenerateWorker
from agents.kingdee_plugin_agent.store.artifact_store import ArtifactStore

# 评估级 mock 规则:命中即视为生成缺陷(模板 token 未渲染)。
EVAL_MOCK_RULES = [
    {"code": "CS9990", "pattern": r"\{\{", "file": "Plugin.cs", "line": 1,
     "message": "未渲染模板占位符残留(unrendered {{TOKEN}})"},
]


def default_mock_compiler() -> MockCompiler:
    """评估用 mock 编译器(EVAL_MOCK_RULES;默认规则对真实插件误报,勿用)。"""
    mc = MockCompiler()
    mc.rules = list(EVAL_MOCK_RULES)
    return mc


def _design_doc(case: dict) -> str:
    return (
        f"# 设计(生成质量 eval)\n\n"
        f"- 需求: {case['requirement']}\n"
        f"- 表单: {case['form_id']}\n"
        f"- 字段: {case['field']}\n"
        f"- 预期触发: {case['expected_trigger']}\n"
    )


def _compile(compile_client, code: str, project_name: str):
    """兼容两种编译客户端契约(多文件协议后统一):

    - CompileClient(agent 侧):有 compile_files → 走单文件委托 compile(code, project_name)
    - CompilerBackend(评估级 MockCompiler):compile(files, project_name) 新协议
    """
    if hasattr(compile_client, "compile_files"):
        return compile_client.compile(code, project_name)
    return compile_client.compile(files=[CompileFile(name="Plugin.cs", code=code)],
                                  project_name=project_name)


def _trigger_ok(case: dict, code: str) -> bool:
    """事件断言:expected_trigger 必须作为 override 方法声明出现在生成代码中。

    用 "override void <trigger>(" 匹配方法声明而非裸子串:设计文档会把
    expected_trigger 文本带入生成代码注释(骨架 BUSINESS_LOGIC 引用设计
    摘要),裸子串会误报;事件真正实现为 override 方法才算挂载正确。
    """
    trigger = case.get("expected_trigger", "")
    return bool(trigger) and f"override void {trigger}(" in code


def run_eval(llm, store: ArtifactStore, cases_dir: Path, compile_client, rag=None) -> dict:
    """跑全部 case:w3 生成 → compile_client 编译 → 事件断言 → eval 报告。

    报告 = {"total", "compile_pass_rate", "trigger_pass_rate",
            "review_reject_rate", "results"}。
    results 每项 {"id", "plugin_type", "compiled", "review_passed",
                  "trigger_ok", "errors"}。
    review_reject_rate 在骨架为 None(w4 审查未跑);真实评估补记审查退回率。

    llm=None 走 w3 确定性骨架(渲染模板全部 token,结果固定);
    compile_client 兼容 CompilerBackend / CompileClient 契约
    (compile(code, project_name) -> CompileResult)。
    单个 case 任何异常(坏 JSON / 缺字段 / 生成失败)按失败记录,不中断整个 eval。
    """
    worker = GenerateWorker(llm=llm, store=store, rag=rag)
    results = []
    for f in sorted(Path(cases_dir).glob("*.json")):
        case_id = f.stem  # 解析前兜底:坏 case 用文件 stem 标识
        plugin_type, compiled, errors, trigger_ok = "unknown", False, [], False
        try:
            case = json.loads(f.read_text(encoding="utf-8"))
            case_id = case["id"]
            plugin_type = case["plugin_type"]
            store.write(case_id, "design.md", _design_doc(case))
            sub = Subtask(id=case_id, plugin_type=plugin_type,
                          title=case["requirement"], deps=[], status="gen_done")
            worker.run(TaskState(requirement_spec={}, todo=[]), sub)
            code = store.read(case_id, "Plugin.cs")
            if not code.strip():
                compiled, errors = False, ["CS9991: 生成代码为空"]
            else:
                cr = _compile(compile_client, code, case_id)
                compiled, errors = cr.success, [e.code for e in cr.errors]
            trigger_ok = _trigger_ok(case, code)
        except Exception as e:  # 单个 case 异常按失败记录,不中断整个 eval
            compiled, errors = False, [f"eval:{type(e).__name__}"]
        results.append({"id": case_id, "plugin_type": plugin_type,
                        "compiled": compiled, "review_passed": False,
                        "trigger_ok": trigger_ok, "errors": errors})
    passed = sum(1 for r in results if r["compiled"])
    trigger_ok_total = sum(1 for r in results if r["trigger_ok"])
    return {"total": len(results),
            "compile_pass_rate": passed / max(len(results), 1),
            "trigger_pass_rate": trigger_ok_total / max(len(results), 1),
            "review_reject_rate": None,  # 骨架未跑 w4;真实评估补记
            "results": results}


def record_baseline(cases_dir: Path | None = None, out: Path | None = None) -> dict:
    """生成并落盘基线(确定性:llm=None + 评估级 mock 编译),返回报告。"""
    cases_dir = Path(cases_dir or (Path(__file__).parent / "cases"))
    out = Path(out or (Path(__file__).parent / "baseline.json"))
    with tempfile.TemporaryDirectory() as td:
        report = run_eval(llm=None, store=ArtifactStore(root=Path(td)),
                          cases_dir=cases_dir, compile_client=default_mock_compiler())
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    rep = record_baseline()
    print(json.dumps(rep, ensure_ascii=False, indent=2))
