import pytest
from agents.kingdee_plugin_agent.store.artifact_store import ArtifactStore, ArtifactStoreError


def test_write_and_read(tmp_path):
    store = ArtifactStore(root=tmp_path)
    p = store.write("A1", "design.md", "# 设计")
    assert p.exists() and p.parent.name == "A1"
    assert store.read("A1", "design.md") == "# 设计"


def test_read_missing_raises(tmp_path):
    store = ArtifactStore(root=tmp_path)
    with pytest.raises(ArtifactStoreError):
        store.read("A1", "nope.md")


from agents.kingdee_plugin_agent.graph.state import Subtask, TaskState, TASK_STATUS


def test_subtask_status_valid():
    s = Subtask(id="A1", plugin_type="bill", title="x", deps=[], status="pending")
    assert s.status in TASK_STATUS


def test_task_state_aggregates():
    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=3)
    st.todo.append(Subtask("A1", "bill", "审核校验", [], "in_progress"))
    assert st.todo[0].status == "in_progress"
    assert st.rework_budget_left == 3


from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase


class DummyWorker(WorkerBase):
    name = "dummy"

    def _execute(self, state, subtask):
        return {"status": "DONE", "artifact_key": "code_path", "evidence": "ok"}


def test_worker_report_format(tmp_path):
    w = DummyWorker(llm=None, store=ArtifactStore(root=tmp_path))
    msg = w._report("DONE", "code_path", "编译通过", "无")
    assert msg.startswith("STATUS: DONE")
    assert "产物: code_path" in msg


def test_worker_run_dispatch(tmp_path):
    w = DummyWorker(llm=None, store=ArtifactStore(root=tmp_path))
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "in_progress")
    new_sub, msg = w.run(st, sub)
    # run() 派发 _execute,结果写入 report(含 worker 名)并格式化上报
    assert new_sub.report["status"] == "DONE"
    assert new_sub.report["worker"] == "dummy"
    assert msg.startswith("STATUS: DONE")


from agents.kingdee_plugin_agent.graph.supervisor import Supervisor


def test_next_ready_respects_deps():
    st = TaskState(requirement_spec={}, todo=[
        Subtask("B1", "service", "y", [], "pending"),           # 无依赖
        Subtask("A1", "bill", "x", ["B1"], "pending"),          # 依赖 B1
    ])
    s = Supervisor(llm=None, workers={})
    nxt = s._next_ready(st)
    assert nxt.id == "B1"  # 先无依赖


def test_next_ready_respects_concurrency():
    st = TaskState(requirement_spec={}, todo=[
        Subtask(f"T{i}", "bill", f"t{i}", [], "in_progress") for i in range(3)
    ] + [Subtask("T3", "bill", "t3", [], "pending")])
    s = Supervisor(llm=None, workers={})
    assert s._next_ready(st) is None  # 并发已达 3


def test_budget_exhausted():
    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=0)
    s = Supervisor(llm=None, workers={})
    assert s._check_budget(st) is False


def test_next_ready_blocks_on_pending_dep():
    st = TaskState(requirement_spec={}, todo=[
        Subtask("A1", "bill", "x", ["B1"], "pending"),          # 依赖 B1,先列出
        Subtask("B1", "service", "y", [], "pending"),           # 无依赖
    ])
    s = Supervisor(llm=None, workers={})
    nxt = s._next_ready(st)
    assert nxt is not None and nxt.id == "B1"  # A1 依赖未满足,不可派发


def test_next_ready_blocks_on_failed_dep():
    st = TaskState(requirement_spec={}, todo=[
        Subtask("A1", "bill", "x", ["B1"], "pending"),          # 依赖 B1
        Subtask("B1", "service", "y", [], "failed"),            # 依赖已失败
    ])
    s = Supervisor(llm=None, workers={})
    assert s._next_ready(st) is None  # failed 依赖永久阻塞(终态处理在 C10)


def test_next_ready_no_shadow_by_blocked_dep():
    st = TaskState(requirement_spec={}, todo=[
        Subtask("A1", "bill", "x", ["B1"], "pending"),          # 被依赖阻塞,但排在前面
        Subtask("B1", "service", "y", [], "pending"),           # 就绪但排在后面
    ])
    s = Supervisor(llm=None, workers={})
    nxt = s._next_ready(st)
    assert nxt is not None and nxt.id == "B1"  # 阻塞项不遮蔽后面的就绪项


from agents.kingdee_plugin_agent.graph.workers.w1_requirement import (
    RequirementWorker,
    build_confirmation_summary,
)


def test_confirmation_summary_lists_decisions_and_assumptions():
    spec = {"decisions": [{"q": "校验字段", "a": "FQty"}],
            "assumptions": ["未说明拦截方式,默认硬拦截"]}
    text = build_confirmation_summary(spec)
    # 决策(问题+答案)与假设必须同时列出,防"点确认但需求已丢"的假确认
    assert "校验字段" in text and "FQty" in text  # 决策:问题与答案都在摘要里
    assert "默认硬拦截" in text                   # 假设清单也在


def test_w1_split_subtasks_deterministic(tmp_path):
    """真实拆解逻辑(终审 C3:替换 tautological 测试):按 spec.plugin_types 拆单子任务。"""
    from agents.kingdee_plugin_agent.graph.workers.w1_requirement import RequirementWorker
    w = RequirementWorker(llm=None, store=ArtifactStore(root=tmp_path))
    st = TaskState(requirement_spec={"requirement": "审核校验插件"}, todo=[])
    todo = w.split_subtasks(st, {"plugin_types": ["bill", "service"], "requirement": "审核校验插件"})
    assert [t.id for t in todo] == ["A1", "B1"]
    assert [t.plugin_type for t in todo] == ["bill", "service"]
    assert todo[0].deps == []  # 无依赖标注


def test_w1_split_subtasks_llm(tmp_path):
    """LLM 拆解:subtasks 契约(id/plugin_type/title/deps)真实生效。"""
    from agents.kingdee_plugin_agent.graph.workers.w1_requirement import (
        RequirementWorker, PlanOutput)
    llm = ScriptedLLM(scripts={PlanOutput: [{"subtasks": [
        {"id": "A1", "plugin_type": "bill", "title": "单据插件", "deps": ["B1"]},
        {"id": "B1", "plugin_type": "service", "title": "服务插件", "deps": []}]}]})
    w = RequirementWorker(llm=llm, store=ArtifactStore(root=tmp_path))
    st = TaskState(requirement_spec={}, todo=[])
    todo = w.split_subtasks(st, {"requirement": "x"})
    assert [t.id for t in todo] == ["A1", "B1"]
    assert todo[0].deps == ["B1"]  # 依赖标注生效


from agents.kingdee_plugin_agent.graph.workers.w2_design import DesignWorker, TYPE_PROMPTS


def test_design_type_prompt_mapping():
    assert TYPE_PROMPTS["bill"].endswith("bill.md")
    assert set(TYPE_PROMPTS) == {"bill", "service", "list"}


def test_design_worker_executes(tmp_path):
    w = DesignWorker(llm=None, store=ArtifactStore(root=tmp_path), rag=None)
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "in_progress")
    sub, msg = w.run(st, sub)
    assert sub.design_path.endswith("design.md")
    assert "STATUS: DONE" in msg


from agents.kingdee_plugin_agent.graph.workers.w3_generate import GenerateWorker
from agents.kingdee_plugin_agent.graph.workers.w4_review import ReviewWorker, VERDICTS


def test_generate_produces_code(tmp_path):
    w = GenerateWorker(llm=None, store=ArtifactStore(root=tmp_path), rag=None)
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "gen_done")
    sub.design_path = str(tmp_path / "A1" / "design.md")
    (tmp_path / "A1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "A1" / "design.md").write_text("# 设计", encoding="utf-8")
    sub, msg = w.run(st, sub)
    assert sub.code_path.endswith("Plugin.cs")


def test_review_verdicts():
    assert set(VERDICTS) == {"Approved", "Needs fixes"}


def test_review_findings_structure(tmp_path):
    w = ReviewWorker(llm=None, store=ArtifactStore(root=tmp_path), rag=None)
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "review_done")
    sub.code_path = str(tmp_path / "A1" / "Plugin.cs")
    (tmp_path / "A1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "A1" / "Plugin.cs").write_text("class X {}", encoding="utf-8")
    sub, msg = w.run(st, sub)
    assert sub.review_verdict in VERDICTS


def test_review_verdict_logic_critical_to_needs_fixes(tmp_path):
    """裁决逻辑真实:代码残留未渲染模板占位符 → Critical → Needs fixes。"""
    import json
    w = ReviewWorker(llm=None, store=ArtifactStore(root=tmp_path), rag=None)
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "review_done")
    (tmp_path / "A1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "A1" / "Plugin.cs").write_text(
        "public class X { /* {{BUSINESS_LOGIC}} */ }", encoding="utf-8")
    sub, msg = w.run(st, sub)
    assert sub.review_verdict == "Needs fixes"
    assert sub.report["path"].endswith("review.json")
    findings = json.loads((tmp_path / "A1" / "review.json").read_text(encoding="utf-8"))
    assert findings and findings[0]["severity"] == "Critical"


from agents.kingdee_plugin_agent.graph.workers.w5_compile import (
    CompileWorker,
    MAX_COMPILE_ROUNDS,
)
from agents.kingdee_plugin_agent.graph.workers.w5_5_smoke import SmokeWorker
from compile_service.models import CompileError, CompileResult, CompileUnavailableError
from agents.kingdee_plugin_agent.tools.smoke_client import SmokeResult


class FakeCompileClient:
    """fail_first 轮失败、其后通过;health 恒可用。"""

    def __init__(self, fail_first=0):
        self.calls = 0
        self.fail_first = fail_first

    def health(self):
        return True

    def compile(self, code, project_name):
        self.calls += 1
        if self.calls <= self.fail_first:
            return CompileResult(success=False, raw_output="", duration_ms=0,
                                 errors=[CompileError("P.cs", 1, "CS0103", "xxx()", True)])
        return CompileResult(success=True, raw_output="", duration_ms=0, errors=[])


def _write_code(tmp_path, sub):
    sub.code_path = str(tmp_path / "A1" / "Plugin.cs")
    (tmp_path / "A1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "A1" / "Plugin.cs").write_text("class X {}", encoding="utf-8")


def test_compile_fix_loop(tmp_path):
    """修复循环真实生效:第 1 轮失败 → 重编 → 第 2 轮通过(2 轮)。"""
    w = CompileWorker(llm=None, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient(fail_first=1))
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    sub, msg = w.run(st, sub)
    assert w.client.calls == 2        # 失败 1 轮 + 重编通过 1 轮
    assert sub.compile_errors == []   # 通过后清空错误
    assert "STATUS: DONE" in msg and "第 2 轮" in msg


def test_compile_service_down_is_blocked(tmp_path):
    """健康探测前置:服务不可用 → BLOCKED,不算编译轮次(不触达 compile)。"""

    class Down:
        def health(self):
            return False

    w = CompileWorker(llm=None, store=ArtifactStore(root=tmp_path), compile_client=Down())
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    sub, msg = w.run(st, sub)
    assert "BLOCKED" in msg  # 服务不可用不算编译轮次
    assert sub.compile_errors == []


def test_compile_503_midway_is_blocked(tmp_path):
    """compile 期间 503(容器中途挂)→ BLOCKED,不算轮次。"""

    class Unavailable:
        def health(self):
            return True

        def compile(self, code, project_name):
            raise CompileUnavailableError("compiler unavailable")

    w = CompileWorker(llm=None, store=ArtifactStore(root=tmp_path),
                      compile_client=Unavailable())
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    sub, msg = w.run(st, sub)
    assert "BLOCKED" in msg and "503" in msg


def test_compile_exhausted_decrements_budget(tmp_path):
    """5 轮全失败 → BLOCKED 并扣全局返工预算 1。"""
    w = CompileWorker(llm=None, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient(fail_first=99))
    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=3)
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    sub, msg = w.run(st, sub)
    assert w.client.calls == MAX_COMPILE_ROUNDS  # 重编上限 5 轮
    assert "BLOCKED" in msg
    assert st.rework_budget_left == 2            # 编译超限扣 1 预算
    assert sub.compile_errors                    # 最后一轮错误留存


def test_compile_error_retrieves_experience(tmp_path):
    """失败轮次检索经验库:命中附到 compile_errors,供 C10 LLM 修复。"""

    class FakeExperience:
        def __init__(self):
            self.searched = []

        def search_related(self, error_code, message, k=3):
            self.searched.append(error_code)
            return [{"text": f"[{error_code}] 缺引用 Kingdee.BOS.Core 修复:加 using",
                     "score": 0.9, "metadata": {}}]

    exp = FakeExperience()
    w = CompileWorker(llm=None, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient(fail_first=99), experience=exp)
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    sub, msg = w.run(st, sub)
    assert exp.searched == ["CS0103"] * MAX_COMPILE_ROUNDS  # 每失败轮次按错误码检索
    assert sub.compile_errors[0]["experience"][0].startswith("[CS0103]")


def test_smoke_ok_no_budget_change(tmp_path):
    class FakeSmoke:
        def deploy_and_verify(self, dll_path, form_id):
            assert form_id == "SAL_SaleOrder"  # 环境里取 form_id
            return SmokeResult(ok=True, detail="assembly 加载 + 映射验证通过")

    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=3)
    st.environment = {"form_id": "SAL_SaleOrder"}
    w = SmokeWorker(llm=None, store=ArtifactStore(root=tmp_path), smoke_client=FakeSmoke())
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    sub, msg = w.run(st, sub)
    assert "STATUS: DONE" in msg
    assert st.rework_budget_left == 3  # 冒烟成功不扣预算


def test_smoke_fail_decrements_budget(tmp_path):
    class FakeSmoke:
        def deploy_and_verify(self, dll_path, form_id):
            return SmokeResult(ok=False, detail="FormId 映射缺失")

    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=3)
    w = SmokeWorker(llm=None, store=ArtifactStore(root=tmp_path), smoke_client=FakeSmoke())
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    sub, msg = w.run(st, sub)
    assert "STATUS: BLOCKED" in msg
    assert st.rework_budget_left == 2  # 冒烟失败扣 1 预算


from agents.kingdee_plugin_agent.graph.workers.w6_package import PackageWorker
from agents.kingdee_plugin_agent.graph.workers.w7_distill import DistillWorker


def test_package_worker_sets_deliverable(tmp_path):
    w = PackageWorker(llm=None, store=ArtifactStore(root=tmp_path), builder=None, output_dir=tmp_path)
    st = TaskState(requirement_spec={}, todo=[Subtask("A1", "bill", "x", [], "packaged")])
    st.todo[0].code_path = str(tmp_path / "A1" / "Plugin.cs")
    (tmp_path / "A1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "A1" / "Plugin.cs").write_text("class X {}", encoding="utf-8")
    sub = Subtask("A1", "bill", "x", [], "packaged")
    sub, msg = w.run(st, sub)
    assert st.final_deliverable.endswith(".zip")


def test_distill_proposes_but_never_blocks(tmp_path):
    from common.rag import RagClient, ExperienceStore
    client = RagClient(data_dir=tmp_path / "rag")
    store = ExperienceStore(client)
    w = DistillWorker(llm=None, store=ArtifactStore(root=tmp_path), experience=store)
    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=0)
    sub, msg = w.run(st, Subtask("A1", "bill", "x", [], "delivered"))
    assert "STATUS: DONE" in msg  # 失败也不阻塞交付


class BrokenExp:
    def propose(self, *a, **k):
        raise RuntimeError("chroma down")


def test_distill_broken_store_never_blocks(tmp_path):
    from agents.kingdee_plugin_agent.graph.workers.w7_distill import DistillWorker
    w = DistillWorker(llm=None, store=ArtifactStore(root=tmp_path), experience=BrokenExp())
    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=0)
    sub = Subtask("A1", "bill", "x", [], "delivered")
    sub.compile_errors = [{"code": "CS0103", "message": "m"}]
    sub, msg = w.run(st, sub)
    assert "DONE_WITH_CONCERNS" in msg
    assert "沉淀失败" in msg


def test_distill_proposes_real_experience(tmp_path):
    """真实经验库落库:compile_errors 逐条 propose,条目 status=proposed、source=w7。"""
    from common.rag import RagClient, ExperienceStore
    client = RagClient(data_dir=tmp_path / "rag")
    exp = ExperienceStore(client)
    w = DistillWorker(llm=None, store=ArtifactStore(root=tmp_path), experience=exp)
    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=0)
    sub = Subtask("A1", "bill", "x", [], "delivered")
    sub.compile_errors = [{"code": "CS0103", "message": "名称 m 不存在"}]
    sub, msg = w.run(st, sub)
    assert "STATUS: DONE" in msg
    hits = client.search("experience", "CS0103", k=5)
    assert hits and hits[0]["metadata"].get("source") == "w7"
    assert hits[0]["metadata"].get("status") == "proposed"


# ═══════════════════════════ C10 主管图构建 ═══════════════════════════
import json as _json
from pathlib import Path as _Path

from langgraph.types import Command

from agents.kingdee_plugin_agent.agent import AGENT_NAME, build_graph, default_recursion_limit
from agents.kingdee_plugin_agent.graph.supervisor import DecideAction, Supervisor, worker_for_subtask
from agents.kingdee_plugin_agent.graph.workers.w1_requirement import (
    PlanOutput, QuestionsOutput, RequirementWorker,
)
from agents.kingdee_plugin_agent.graph.workers.w2_design import DesignOutput, DesignWorker
from agents.kingdee_plugin_agent.graph.workers.w3_generate import CodeOutput, GenerateWorker
from agents.kingdee_plugin_agent.graph.workers.w4_review import ReviewOutput, ReviewWorker
from agents.kingdee_plugin_agent.graph.workers.w5_5_smoke import SmokeWorker
from agents.kingdee_plugin_agent.graph.workers.w5_compile import CompileWorker


class ScriptedLLM:
    """按 schema 弹出脚本化结构化输出;脚本耗尽返回 default(通常用于主管 DecideAction)。

    不 mock LangGraph —— 图结构/路由/interrupt/send/终态全部真实执行,
    只替换 LLM 输出(LLM 调用点可注入,铁律:不要 mock LangGraph 本身)。
    """

    def __init__(self, scripts: dict | None = None, default=None):
        self.scripts = {k: list(v) for k, v in (scripts or {}).items()}
        self.default = default
        self.calls = []

    def with_structured_output(self, schema, **kwargs):
        return _ScriptedStructured(self, schema)


class _ScriptedStructured:
    def __init__(self, parent, schema):
        self.parent, self.schema = parent, schema

    def invoke(self, *args, **kwargs):
        self.parent.calls.append(args)
        queue = self.parent.scripts.get(self.schema)
        if queue:
            raw = queue.pop(0)
            return raw if isinstance(raw, self.schema) else self.schema(**raw)
        if self.parent.default is not None:
            raw = self.parent.default
            return raw if isinstance(raw, self.schema) else self.schema(**raw)
        raise AssertionError(f"{self.schema.__name__} 结构化输出脚本耗尽")


class _OkSmoke:
    def __init__(self, ok=True, detail="assembly 加载 + 映射验证通过"):
        self.ok = ok
        self.detail = detail

    def deploy_and_verify(self, dll_path, form_id):
        return SmokeResult(ok=self.ok, detail=self.detail)


def _cfg(thread: str, todo_count: int = 1) -> dict:
    """运行时 config:recursion_limit 按子任务数预算(设计 §6.2),非 compile 参数。"""
    return {"configurable": {"thread_id": thread},
            "recursion_limit": default_recursion_limit(todo_count)}


def _status_map(todo) -> dict:
    return {t.id: (t.status if hasattr(t, "status") else t["status"]) for t in todo}


# ── 图可达性:完整流水线(澄清 interrupt → spec → 拆解 → 设计 → 生成 → 审查
#    → 编译 → 冒烟 → 打包 → 沉淀 → finish)───────────────────────────────

def test_graph_full_flow_to_finish(tmp_path):
    """真实图全链路:fake LLM 脚本化 + fake 编译/冒烟,interrupt/resume 驱动澄清。"""
    llm = ScriptedLLM(scripts={
        QuestionsOutput: [{"questions": ["目标单据 FormId?", "校验规则?"]}],
        PlanOutput: [{"subtasks": [{"id": "A1", "plugin_type": "bill",
                                    "title": "销售订单审核校验", "deps": []}]}],
        DesignOutput: [{"design_markdown": "# 设计:A1\n数量>0 校验"}],
        CodeOutput: [{"code": "public class A1 : AbstractBillPlugIn {}"},
                     {"code": "public class A1 : AbstractBillPlugIn { /* 修复 */ }"}],
        ReviewOutput: [{"findings": []}],
    }, default={"action": "run"})
    app = build_graph(llm=llm, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient(fail_first=1),
                      smoke_client=_OkSmoke(), output_dir=tmp_path)
    cfg = _cfg("happy")
    initial = {"requirement_spec": {"requirement": "销售订单审核校验插件"}, "todo": []}

    # 澄清:逐问 interrupt → 答案 resume
    r = app.invoke(initial, cfg)
    assert r["__interrupt__"][0].value["type"] == "question"
    assert r["__interrupt__"][0].value["round"] == 0
    r = app.invoke(Command(resume="SAL_SaleOrder"), cfg)
    assert r["__interrupt__"][0].value["round"] == 1
    r = app.invoke(Command(resume="数量必须大于 0"), cfg)
    # 问题问完 → 确认摘要(决策 + 假设都在)
    assert r["__interrupt__"][0].value["type"] == "confirm"
    assert "SAL_SaleOrder" in r["__interrupt__"][0].value["summary"]
    r = app.invoke(Command(resume="确认"), cfg)

    # spec 确认 → 拆解 → 全流水线自动跑完
    assert r["spec_confirmed"] is True
    assert _status_map(r["todo"]) == {"A1": "delivered"}
    assert r["action"] == "finish"
    assert r["rework_budget_left"] == 3          # 无返工不扣预算
    assert r["final_deliverables"]               # 交付包已记录
    assert r["final_deliverable"].endswith(".zip")
    # spec 落盘为 JSON(终审 C5:非 repr)
    spec = _json.loads((tmp_path / "requirement" / "spec.json").read_text(encoding="utf-8"))
    assert spec["decisions"][0]["a"] == "SAL_SaleOrder"


def test_graph_rework_loop_review_needs_fixes(tmp_path):
    """w4 Needs fixes → needs_rework → w3 重新生成 → w4 Approved → 走完 finish,预算扣 1。"""
    llm = ScriptedLLM(scripts={
        QuestionsOutput: [{"questions": ["FormId?"]}],
        PlanOutput: [{"subtasks": [{"id": "A1", "plugin_type": "bill", "title": "x", "deps": []}]}],
        DesignOutput: [{"design_markdown": "# 设计"}],
        CodeOutput: [{"code": "class A1 {}"}, {"code": "class A1 { /* 重写 */ }"}],
        ReviewOutput: [{"findings": [{"severity": "Critical", "issue": "缺校验"}]},
                       {"findings": []}],
    }, default={"action": "run"})
    app = build_graph(llm=llm, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient(), smoke_client=_OkSmoke(),
                      output_dir=tmp_path)
    cfg = _cfg("rework")
    r = app.invoke({"requirement_spec": {"requirement": "x"}, "todo": []}, cfg)
    r = app.invoke(Command(resume="SAL_SaleOrder"), cfg)
    r = app.invoke(Command(resume="确认"), cfg)
    assert r["action"] == "finish"
    assert _status_map(r["todo"]) == {"A1": "delivered"}
    assert r["rework_budget_left"] == 2          # 审查重工 1 轮扣 1


def test_graph_midrun_ask_user_interrupt(tmp_path):
    """主管 LLM 决策 ask_user:问题 interrupt → 用户答复记入 user_feedback 后继续派发。"""
    llm = ScriptedLLM(scripts={
        DecideAction: [{"action": "ask_user", "question": "校验规则确认?"}],
        DesignOutput: [{"design_markdown": "# 设计"}],
        CodeOutput: [{"code": "class A1 {}"}],
        ReviewOutput: [{"findings": []}],
    }, default={"action": "run"})
    app = build_graph(llm=llm, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient(), smoke_client=_OkSmoke(),
                      output_dir=tmp_path)
    cfg = _cfg("midask")
    initial = {"requirement_spec": {"requirement": "x"},
               "todo": [Subtask("A1", "bill", "x", [], "pending")],
               "spec_confirmed": True}
    r = app.invoke(initial, cfg)
    assert r["__interrupt__"][0].value["type"] == "ask_user"
    assert r["__interrupt__"][0].value["question"] == "校验规则确认?"
    r = app.invoke(Command(resume="按 0 校验"), cfg)
    assert r["user_feedback"] == ["按 0 校验"]
    assert r["action"] == "finish"
    assert _status_map(r["todo"]) == {"A1": "delivered"}


# ── 终态处理(终审 C4):finish / fail / 失败依赖传递 ────────────────────

def test_graph_all_delivered_finishes(tmp_path):
    app = build_graph(llm=None, store=ArtifactStore(root=tmp_path), output_dir=tmp_path)
    r = app.invoke({"requirement_spec": {"requirement": "x"},
                    "todo": [Subtask("A1", "bill", "x", [], "delivered")]}, _cfg("fin"))
    assert r["action"] == "finish"


def test_graph_budget_exhausted_fails(tmp_path):
    """返工预算耗尽 + 剩余工作 → fail,剩余子任务标记 failed。"""
    app = build_graph(llm=None, store=ArtifactStore(root=tmp_path), output_dir=tmp_path)
    r = app.invoke({"requirement_spec": {"requirement": "x"},
                    "todo": [Subtask("A1", "bill", "x", [], "pending")],
                    "rework_budget_left": 0}, _cfg("budget"))
    assert r["action"].startswith("fail")
    assert _status_map(r["todo"]) == {"A1": "failed"}


def test_graph_failed_dep_marks_dependents_failed(tmp_path):
    """依赖失败传递:dep failed → pending 依赖者标记 failed → fail(不派发被阻塞者)。"""
    app = build_graph(llm=None, store=ArtifactStore(root=tmp_path), output_dir=tmp_path)
    r = app.invoke({"requirement_spec": {"requirement": "x"}, "todo": [
        Subtask("B1", "service", "y", [], "failed"),
        Subtask("A1", "bill", "x", ["B1"], "pending"),
    ]}, _cfg("depfail"))
    assert r["action"].startswith("fail")
    assert _status_map(r["todo"]) == {"A1": "failed", "B1": "failed"}


def test_supervisor_decide_terminal_logic():
    """主管 decide 终态确定性子集(finish/fail),依赖失败传递在派发前生效。"""
    s = Supervisor(llm=None, workers={})
    st = TaskState(requirement_spec={}, todo=[Subtask("A1", "bill", "x", [], "delivered")])
    assert s.decide(st) == "finish"
    st2 = TaskState(requirement_spec={}, todo=[Subtask("A1", "bill", "x", [], "pending")],
                    rework_budget_left=0)
    assert s.decide(st2).startswith("fail")
    assert st2.todo[0].status == "failed"
    st3 = TaskState(requirement_spec={}, todo=[
        Subtask("B1", "service", "y", [], "failed"),
        Subtask("A1", "bill", "x", ["B1"], "pending")])
    assert s.decide(st3).startswith("fail")
    assert {t.id: t.status for t in st3.todo} == {"A1": "failed", "B1": "failed"}


def test_worker_for_subtask_mapping():
    """状态生命周期 → 阶段 worker 映射(终审 C4 契约)。"""
    assert worker_for_subtask(Subtask("A1", "bill", "x", [], "pending")) == "w2"
    assert worker_for_subtask(Subtask("A1", "bill", "x", [], "design_done")) == "w3"
    assert worker_for_subtask(Subtask("A1", "bill", "x", [], "gen_done")) == "w4"
    assert worker_for_subtask(Subtask("A1", "bill", "x", [], "needs_rework")) == "w3"
    assert worker_for_subtask(Subtask("A1", "bill", "x", [], "review_done")) == "w5"
    assert worker_for_subtask(Subtask("A1", "bill", "x", [], "compile_done")) == "w5_5"
    assert worker_for_subtask(Subtask("A1", "bill", "x", [], "smoke_done")) == "w6"
    assert worker_for_subtask(Subtask("A1", "bill", "x", [], "packaged")) == "w7"
    assert worker_for_subtask(Subtask("A1", "bill", "x", [], "blocked")) == "w1"
    assert worker_for_subtask(Subtask("A1", "bill", "x", [], "delivered")) is None
    assert worker_for_subtask(Subtask("A1", "bill", "x", [], "failed")) is None
    assert worker_for_subtask(Subtask("A1", "bill", "x", [], "in_progress")) is None


# ── 并行派发(send() fan-out)与多子任务交付包合并 ──────────────────────

def test_graph_parallel_dispatch_two_independent_subtasks(tmp_path):
    """send() 并行:两个无依赖子任务同一超步同时 in_progress(≤MAX_PARALLEL),最终合并交付。"""
    app = build_graph(llm=None, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient(), smoke_client=_OkSmoke(),
                      output_dir=tmp_path)
    cfg = _cfg("para", todo_count=2)
    initial = {"requirement_spec": {"requirement": "复合需求"},
               "todo": [Subtask("A1", "bill", "a"), Subtask("B1", "service", "b")]}
    seen_in_progress = False
    final = None
    for chunk in app.stream(initial, cfg, stream_mode="values"):
        statuses = _status_map(chunk["todo"])
        if statuses == {"A1": "in_progress", "B1": "in_progress"}:
            seen_in_progress = True   # dispatcher 超步:两个同时 in_progress
        final = chunk
    assert seen_in_progress, "两个独立子任务应同一步 in_progress(send 并行派发)"
    assert final["action"] == "finish"
    assert _status_map(final["todo"]) == {"A1": "delivered", "B1": "delivered"}
    # 多子任务交付包合并(v1 逐包):两个包都记录,文件名互不覆盖
    assert len(final["final_deliverables"]) == 2
    assert len(set(final["final_deliverables"])) == 2


def test_graph_parallel_caps_at_max_parallel(tmp_path):
    """并发上限:4 个独立子任务同一轮只派 3 个 in_progress,第 4 个排队等空位。"""
    app = build_graph(llm=None, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient(), smoke_client=_OkSmoke(),
                      output_dir=tmp_path)
    cfg = _cfg("cap", todo_count=4)
    initial = {"requirement_spec": {"requirement": "x"},
               "todo": [Subtask(f"T{i}", "bill", f"t{i}") for i in range(4)]}
    max_in_progress = 0
    final = None
    for chunk in app.stream(initial, cfg, stream_mode="values"):
        n = sum(1 for st in _status_map(chunk["todo"]).values() if st == "in_progress")
        max_in_progress = max(max_in_progress, n)
        final = chunk
    assert max_in_progress <= 3                    # MAX_PARALLEL 生效
    assert final["action"] == "finish"
    assert len(final["final_deliverables"]) == 4


# ── 各终审 carry-over 修复点 ──────────────────────────────────────────

def test_graph_unknown_plugin_type_friendly_error(tmp_path):
    """终审 C6:未知 plugin_type → worker ERROR 上报 → 子任务 failed → fail(无 KeyError 裸抛)。"""
    app = build_graph(llm=None, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient(), smoke_client=_OkSmoke(),
                      output_dir=tmp_path)
    r = app.invoke({"requirement_spec": {"requirement": "x"},
                    "todo": [Subtask("A1", "weird", "x")]}, _cfg("unk"))
    assert r["action"].startswith("fail")
    assert _status_map(r["todo"]) == {"A1": "failed"}
    assert "未知插件类型" in r["todo"][0].report.get("concerns", "")


def test_w2_unknown_type_no_keyerror(tmp_path):
    w = DesignWorker(llm=None, store=ArtifactStore(root=tmp_path))
    sub = Subtask("A1", "weird", "x", [], "pending")
    sub, msg = w.run(TaskState(requirement_spec={}, todo=[]), sub)
    assert sub.report["status"] == "ERROR"
    assert "未知插件类型" in msg


def test_w4_review_artifact_key_is_path_field(tmp_path):
    """终审 C7:审查产物走 review_path 字段,不再覆写 run()(基类契约原样)。"""
    w = ReviewWorker(llm=None, store=ArtifactStore(root=tmp_path), rag=None)
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "review_done")
    (tmp_path / "A1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "A1" / "Plugin.cs").write_text("class X {}", encoding="utf-8")
    sub, msg = w.run(st, sub)
    assert sub.review_path.endswith("review.json")   # 路径进 review_path
    assert sub.review_verdict in VERDICTS            # 裁决值不被路径冲掉


def test_w5_llm_fix_rewrites_code(tmp_path):
    """终审 C8:编译失败 → LLM 改写代码写回重编(非原样重提交)。"""
    store = ArtifactStore(root=tmp_path)
    llm = ScriptedLLM(scripts={CodeOutput: [{"code": "class X { /* 修复 */ }"}]})
    client = FakeCompileClient(fail_first=1)
    w = CompileWorker(llm=llm, store=store, compile_client=client)
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    sub, msg = w.run(st, sub)
    assert client.calls == 2                        # 失败 1 轮 + 改写后通过 1 轮
    assert "STATUS: DONE" in msg
    assert "修复" in store.read("A1", "Plugin.cs")  # 改写后的代码落盘


def test_smoke_worker_passes_path(tmp_path):
    """终审 C8:SmokeWorker 传 Path 给 deploy_and_verify(契约是 Path 非 str)。"""

    class PathAssertSmoke:
        def deploy_and_verify(self, dll_path, form_id):
            assert isinstance(dll_path, _Path)
            return SmokeResult(ok=True, detail="ok")

    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=3)
    w = SmokeWorker(llm=None, store=ArtifactStore(root=tmp_path),
                    smoke_client=PathAssertSmoke())
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    sub, msg = w.run(st, sub)
    assert "STATUS: DONE" in msg


def test_w1_spec_persisted_as_json(tmp_path):
    """终审 C5:spec/plan 落盘 JSON(非 repr)。"""
    store = ArtifactStore(root=tmp_path)
    w = RequirementWorker(llm=None, store=store)
    spec = {"decisions": [{"q": "FormId", "a": "SAL_SaleOrder"}], "assumptions": ["默认硬拦截"]}
    w.persist(spec, [Subtask("A1", "bill", "x")])
    raw = (tmp_path / "requirement" / "spec.json").read_text(encoding="utf-8")
    assert _json.loads(raw) == spec                 # JSON 往返一致
    plan = _json.loads((tmp_path / "requirement" / "plan.json").read_text(encoding="utf-8"))
    assert plan[0]["id"] == "A1" and plan[0]["plugin_type"] == "bill"


def test_w1_interrupt_message_and_record_answer(tmp_path):
    """w1 澄清循环接口:interrupt_message 逐题出题,record_answer 记录,问完转确认摘要。"""
    w = RequirementWorker(llm=None, store=ArtifactStore(root=tmp_path))
    st = TaskState(requirement_spec={}, todo=[], clarify_questions=["Q1", "Q2"])
    assert w.interrupt_message(st) == "Q1"
    w.record_answer(st, "A1")
    assert st.clarify_answers == ["A1"]
    st.clarify_round = 1
    assert w.interrupt_message(st) == "Q2"
    st.clarify_round = 2
    assert "需求确认摘要" in w.interrupt_message(st)   # 问题问完 → 确认摘要


def test_agent_name_and_recursion_formula():
    assert AGENT_NAME == "kingdee_plugin_agent"
    assert default_recursion_limit(0) == 50
    assert default_recursion_limit(3) == 80


# ── C10 复审修复(Important 1/2)─────────────────────────────────────────

def test_supervisor_blocked_subtask_not_in_ready_batch():
    """复审 Important 1:blocked 子任务不进就绪批 → decide 走 ask_user,不陷入 run:<id> 忙循环。"""
    s = Supervisor(llm=None, workers={})
    st = TaskState(requirement_spec={}, todo=[Subtask("A1", "bill", "x", [], "blocked")])
    assert s._ready_batch(st) == []
    assert s.decide(st) == "ask_user"   # 确定性兜底 → 问用户,不再发 run:<A1>


def test_graph_blocked_subtask_no_busy_loop(tmp_path):
    """复审 Important 1:blocked 子任务 invoke 挂起在 w1 interrupt,不触 GraphRecursionError。"""
    from langgraph.errors import GraphRecursionError
    app = build_graph(llm=None, store=ArtifactStore(root=tmp_path), output_dir=tmp_path)
    cfg = {"configurable": {"thread_id": "blk"}, "recursion_limit": 40}
    try:
        r = app.invoke({"requirement_spec": {},
                        "todo": [Subtask("A1", "bill", "x", [], "blocked")],
                        "spec_confirmed": True}, cfg)
    except GraphRecursionError:
        raise AssertionError("blocked 子任务触发 supervisor↔dispatcher 忙循环(GraphRecursionError)")
    assert r["__interrupt__"]                                   # 挂起在 ask_user,非忙循环
    assert r["__interrupt__"][0].value["type"] == "ask_user"


def test_langgraph_json_includes_compile_service():
    """复审 Important 2:agent.py 引 compile_service.models,langgraph.json 依赖需登记。"""
    d = _json.loads(_Path("langgraph.json").read_text(encoding="utf-8"))
    assert "./compile_service" in d["dependencies"]
    assert d["graphs"]["kingdee_plugin_agent"] == "./agents/kingdee_plugin_agent/agent.py:build_graph"


# ═══════════════════════════ C11 CLI 入口 ═══════════════════════════
import agents.kingdee_plugin_agent.cli as _cli

from agents.kingdee_plugin_agent.cli import run_cli


def test_cli_requires_env(monkeypatch, capsys):
    """无 KD_BASE_URL = 环境硬门槛:报错退出(1),不进入图执行。"""
    monkeypatch.delenv("KD_BASE_URL", raising=False)
    code = run_cli(["给采购单审核加库存校验", "--env", "test"])
    assert code == 1  # 无环境 = 硬门槛退出
    out = capsys.readouterr().out
    assert "环境" in out


def test_cli_runs_to_finish_with_env(tmp_path, monkeypatch, capsys):
    """有环境(KD_BASE_URL)→ 交互澄清循环 → 确定性流水线跑完 → TodoList + 交付包,返回 0。

    确定性门:monkeypatch cli.build_graph → build_graph(llm=None + fake 编译/冒烟),
    与 C10 图测试同一注入思路(只注入 LLM/外部服务,不 mock LangGraph 本身)。
    stdin 逐次喂澄清答案(1 个问题 + 1 次确认),capsys 校验各阶段输出。
    """
    monkeypatch.setenv("KD_BASE_URL", "http://kd-test:8080")
    answers = iter(["SAL_SaleOrder", "确认"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(
        _cli, "build_graph",
        lambda: build_graph(llm=None, store=ArtifactStore(root=tmp_path),
                            compile_client=FakeCompileClient(), smoke_client=_OkSmoke(),
                            output_dir=tmp_path))
    code = run_cli(["给采购单审核加库存校验", "--env", "test"])
    out = capsys.readouterr().out
    assert code == 0                                  # 全流程跑完返回 0
    assert "需求: 给采购单审核加库存校验" in out
    assert "[澄清 1]" in out                          # 交互澄清循环真实执行
    assert "需求确认摘要" in out                       # 确认摘要已展示给用户
    assert "TodoList 摘要" in out                     # TodoList 摘要已打印
    assert ".zip" in out                              # 交付包路径已打印
