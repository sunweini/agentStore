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


def test_spec_split_subtasks():
    spec = {"plugin_types": ["bill", "service"],
            "subtasks": [{"id": "A", "plugin_type": "bill", "deps": ["B"]},
                          {"id": "B", "plugin_type": "service", "deps": []}]}
    assert spec["subtasks"][0]["deps"] == ["B"]


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
