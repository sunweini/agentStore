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


def test_artifact_store_rejects_path_traversal_id(tmp_path):
    """终审 Important:w1 拆解由 LLM 生成子任务 id,`..`/`/` 携带 id → 拒绝,
    不越出 artifacts 根目录写文件(原实现直接 Path 拼接)。"""
    store = ArtifactStore(root=tmp_path)
    for bad in ("A/../B", "../A", "A/B", "A\\B"):
        with pytest.raises(ArtifactStoreError):
            store.write(bad, "Plugin.cs", "class X {}")
        with pytest.raises(ArtifactStoreError):
            store.read(bad, "Plugin.cs")
        with pytest.raises(ArtifactStoreError):
            store.paths(bad)
    # 白名单内 id 仍正常
    assert store.write("A1", "Plugin.cs", "x").parent.name == "A1"
    assert store.write("A_1-B2", "p.cs", "y").parent.name == "A_1-B2"


from agents.kingdee_plugin_agent.graph.state import Subtask, TaskState, TASK_STATUS


def test_subtask_status_valid():
    s = Subtask(id="A1", plugin_type="bill", title="x", deps=[], status="pending")
    assert s.status in TASK_STATUS


def test_task_state_aggregates():
    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=3)
    st.todo.append(Subtask("A1", "bill", "审核校验", [], "in_progress"))
    assert st.todo[0].status == "in_progress"
    assert st.rework_budget_left == 3


def test_subtask_contract_fields_default():
    """下发模板字段(设计 §5.1)默认值:验收标准空 = 按需求确认摘要验收,
    max_rework 0 = 全局默认 GLOBAL_REWORK_BUDGET,rework_count 从 0 起。"""
    s = Subtask(id="A1", plugin_type="bill", title="x")
    assert s.acceptance_criteria == ""
    assert s.max_rework == 0
    assert s.rework_count == 0


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
    """返工预算耗尽且有未交付工作 → decide 确定性 fail(原 _check_budget 死代码
    清理,等价预算判定在 _decide 第 4 步,与扣减逻辑同处)。"""
    st = TaskState(requirement_spec={}, todo=[Subtask("A1", "bill", "x", [], "pending")],
                   rework_budget_left=0)
    s = Supervisor(llm=None, workers={})
    assert s.decide(st) == "fail:返工预算耗尽"
    assert st.todo[0].status == "failed"   # 剩余子任务标记 failed(与扣减同语义)


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


def test_w1_split_llm_acceptance_fields_pass_through(tmp_path):
    """LLM 拆解 schema 新增验收标准/上限字段(设计 §5.1):真实传入 Subtask,
    LLM 未给时确定性兜底(按需求确认摘要验收 / 0 = 全局默认)。"""
    from agents.kingdee_plugin_agent.graph.workers.w1_requirement import (
        RequirementWorker, PlanOutput)
    llm = ScriptedLLM(scripts={PlanOutput: [{"subtasks": [
        {"id": "A1", "plugin_type": "bill", "title": "单据插件", "deps": [],
         "acceptance_criteria": "库存数量>0 时审核通过",
         "max_rework": 2},
        {"id": "B1", "plugin_type": "service", "title": "服务插件", "deps": []}]}]})
    w = RequirementWorker(llm=llm, store=ArtifactStore(root=tmp_path))
    todo = w.split_subtasks(TaskState(requirement_spec={}, todo=[]), {"requirement": "x"})
    assert todo[0].acceptance_criteria == "库存数量>0 时审核通过"
    assert todo[0].max_rework == 2
    # LLM 未给字段 → 确定性兜底值
    assert todo[1].acceptance_criteria == "按需求确认摘要验收"
    assert todo[1].max_rework == 0


def test_w1_split_fallback_acceptance_fields(tmp_path):
    """确定性拆解(llm=None)同样带下发模板默认字段。"""
    from agents.kingdee_plugin_agent.graph.workers.w1_requirement import RequirementWorker
    w = RequirementWorker(llm=None, store=ArtifactStore(root=tmp_path))
    todo = w.split_subtasks(TaskState(requirement_spec={}, todo=[]),
                            {"plugin_types": ["bill"], "requirement": "审核校验插件"})
    assert todo[0].acceptance_criteria == "按需求确认摘要验收"
    assert todo[0].max_rework == 0
    assert todo[0].rework_count == 0


def test_w1_extract_form_id_explicit_slot():
    """FormId 显式槽优先(split 输出 LLM 归纳)。"""
    from agents.kingdee_plugin_agent.graph.workers.w1_requirement import RequirementWorker
    w = RequirementWorker(llm=None, store=None)
    assert w.extract_form_id({"form_id": "SAL_SaleOrder", "decisions": []}) == "SAL_SaleOrder"
    assert w.extract_form_id({"form_id": "  PUR_ReceiveBill  ", "decisions": []}) == "PUR_ReceiveBill"


def test_w1_extract_form_id_from_decision():
    """兜底:decisions 中"单据/FormId"问题的答案取首个标识符 token(llm=None 路径)。"""
    from agents.kingdee_plugin_agent.graph.workers.w1_requirement import RequirementWorker
    w = RequirementWorker(llm=None, store=None)
    spec = {"decisions": [
        {"q": "请描述该插件的核心业务场景与目标单据(FormId),以及期望的关键行为。",
         "a": "销售订单审核插件 SAL_SaleOrder"},
        {"q": "校验规则?", "a": "数量必须大于 0"},
    ]}
    assert w.extract_form_id(spec) == "SAL_SaleOrder"
    # 无单据相关问题 → 空(冒烟按无 form_id 处理)
    assert w.extract_form_id({"decisions": [{"q": "校验规则?", "a": "数量大于 0"}]}) == ""
    # 评审 Minor:答案里带 "FormId" 词本身 → 跳过该 token,取真正的单据号
    assert w.extract_form_id({"decisions": [
        {"q": "目标单据是?", "a": "单据 FormId 是 SAL_SaleOrder"}]}) == "SAL_SaleOrder"
    assert w.extract_form_id({"decisions": [{"q": "单据?", "a": "FormId"}]}) == ""


def test_w1_split_llm_form_id_written_to_spec(tmp_path):
    """LLM 拆解输出带 form_id → 回写 spec["form_id"](冒烟链路槽)。"""
    from agents.kingdee_plugin_agent.graph.workers.w1_requirement import (
        RequirementWorker, PlanOutput)
    llm = ScriptedLLM(scripts={PlanOutput: [{"subtasks": [
        {"id": "A1", "plugin_type": "bill", "title": "单据插件", "deps": []}],
        "form_id": "SAL_SaleOrder"}]})
    w = RequirementWorker(llm=llm, store=ArtifactStore(root=tmp_path))
    spec = {"requirement": "x"}
    w.split_subtasks(TaskState(requirement_spec={}, todo=[]), spec)
    assert spec["form_id"] == "SAL_SaleOrder"


from agents.kingdee_plugin_agent.graph.workers.w2_design import (
    DesignOutput, DesignWorker, TYPE_PROMPTS,
)


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


def test_w2_experience_hits_reach_design_context(tmp_path):
    """经验库命中注入设计 LLM 上下文:human 消息含"历史踩坑参考"标记与命中文本,
    标题作双信号检索(k=3),verified 条目排在 proposed 之前(历史坑 → 设计规避)。"""
    from pathlib import Path

    class FakeExperience:
        def __init__(self):
            self.calls = []

        def search_related(self, error_code, message, k=3):
            self.calls.append((error_code, message, k))
            return [
                {"text": "[CS0506] 基类无此成员 修复:核对基类事件签名", "score": 0.8,
                 "metadata": {"status": "proposed", "confidence": "unverified"}},
                {"text": "[CS0115] 找不到可重写方法 修复:确认事件名与签名一致", "score": 0.7,
                 "metadata": {"status": "verified", "confidence": "verified"}},
            ]

    class _DesignLLM:
        """捕获消息的 fake:with_structured_output 返回自身,invoke 返回契约对象。"""

        def __init__(self):
            self.seen = []

        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages):
            self.seen.append(messages)
            return DesignOutput(design_markdown="# 设计文档(历史坑已规避)")

    exp = FakeExperience()
    w = DesignWorker(llm=_DesignLLM(), store=ArtifactStore(root=tmp_path),
                     experience=exp)
    sub = Subtask("A1", "bill", "审核校验", [], "pending")
    sub, msg = w.run(TaskState(requirement_spec={}, todo=[]), sub)
    assert "STATUS: DONE" in msg
    assert exp.calls == [("审核校验", "审核校验", 3)]  # 标题双信号语义 + k=3
    assert "历史坑已规避" in Path(sub.design_path).read_text(encoding="utf-8")  # 设计落盘
    human = next(m.content for m in w.llm.seen[0] if getattr(m, "type", "") == "human")
    assert "历史踩坑参考" in human
    assert "[CS0115] 找不到可重写方法" in human and "[CS0506] 基类无此成员" in human
    assert human.index("[CS0115]") < human.index("[CS0506]")  # verified 优先


def test_w2_experience_failure_degrades_to_done(tmp_path):
    """经验库故障 → 设计仍 DONE:检索异常被降级为空命中,LLM 照常走设计路径。

    (评审修复:原实现 llm=None 在 _llm_design 的 llm 守卫处提前返回,search_related
    从未被调用,降级逻辑未测;现走真实 LLM 路径 —— seen 非空证明检索异常未阻塞
    LLM 调用,design.md 为 LLM 产出证明非骨架路径。)
    """
    from pathlib import Path

    class BrokenExperience:
        def search_related(self, error_code, message, k=3):
            raise RuntimeError("chroma 不可用")

    class _DesignLLM:
        """捕获消息的 fake:with_structured_output 返回自身,invoke 返回契约对象。"""

        def __init__(self):
            self.seen = []

        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages):
            self.seen.append(messages)
            return DesignOutput(design_markdown="# 设计文档(LLM 正常产出)")

    llm = _DesignLLM()
    w = DesignWorker(llm=llm, store=ArtifactStore(root=tmp_path), rag=None,
                     experience=BrokenExperience())
    sub = Subtask("A1", "bill", "x", [], "pending")
    sub, msg = w.run(TaskState(requirement_spec={}, todo=[]), sub)
    assert "STATUS: DONE" in msg
    assert llm.seen  # 检索异常被降级为空命中,LLM 仍被调用(未阻塞设计)
    human = next(m.content for m in llm.seen[0] if getattr(m, "type", "") == "human")
    assert "历史踩坑参考" not in human  # 降级为空命中,无踩坑段注入
    assert "LLM 正常产出" in Path(sub.design_path).read_text(encoding="utf-8")  # 走 LLM 路径,非确定性骨架
    assert sub.design_path.endswith("design.md")


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


def test_w4_review_context_includes_acceptance_criteria(tmp_path):
    """审查对照验收标准(设计 §5.1):subtask.acceptance_criteria 非空时进 LLM
    context(human 消息 JSON 键 + 对照提示),不是只看规范库。"""
    import json as _j
    from langchain_core.messages import HumanMessage

    class _CaptureLLM:
        def __init__(self):
            self.seen = []

        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages):
            self.seen.append(messages)
            return ReviewOutput(findings=[])

    w = ReviewWorker(llm=_CaptureLLM(), store=ArtifactStore(root=tmp_path), rag=None)
    sub = Subtask("A1", "bill", "审核校验", [], "gen_done",
                  acceptance_criteria="库存数量>0 时审核拦截")
    (tmp_path / "A1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "A1" / "Plugin.cs").write_text("class X {}", encoding="utf-8")
    sub, msg = w.run(TaskState(requirement_spec={}, todo=[]), sub)
    assert "STATUS: DONE" in msg
    for m in w.llm.seen[0]:
        if isinstance(m, HumanMessage):
            assert "acceptance_criteria" in m.content
            assert "库存数量>0 时审核拦截" in m.content
            assert "验收标准" in m.content
            break
    else:
        raise AssertionError("未找到 human 消息")
    ctx = _j.loads(w.llm.seen[0][-1].content.split("代码与规范:", 1)[1].strip().split("\n\n")[0])
    assert ctx["acceptance_criteria"] == "库存数量>0 时审核拦截"


def test_w4_review_context_criteria_empty_default(tmp_path):
    """验收标准为空时:context 键存在但为空串,不追加对照提示(不误导 LLM)。"""
    import json as _j
    from langchain_core.messages import HumanMessage

    captured = {}

    class _CaptureLLM:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages):
            captured["human"] = next((m.content for m in messages
                                      if isinstance(m, HumanMessage)), "")
            return ReviewOutput(findings=[])

    w = ReviewWorker(llm=_CaptureLLM(), store=ArtifactStore(root=tmp_path), rag=None)
    sub = Subtask("A1", "bill", "x", [], "gen_done")   # acceptance_criteria 默认空
    (tmp_path / "A1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "A1" / "Plugin.cs").write_text("class X {}", encoding="utf-8")
    sub, msg = w.run(TaskState(requirement_spec={}, todo=[]), sub)
    ctx = _j.loads(captured["human"].split("代码与规范:", 1)[1].strip().split("\n\n")[0])
    assert ctx["acceptance_criteria"] == ""
    assert "验收标准" not in captured["human"].split("\n\n", 1)[-1]


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


def _write_dll(tmp_path, sub):
    """给子任务写一份假 DLL 并记 dll_path(w5.5 冒烟验证对象,结构级修复后)。"""
    dll = tmp_path / "A1" / "Plugin.dll"
    dll.parent.mkdir(parents=True, exist_ok=True)
    dll.write_bytes(b"PE\x00\x00")
    sub.dll_path = str(dll)


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


def test_compile_http_error_midway_is_blocked_no_budget(tmp_path):
    """终审 Important:compile 期间超时/连接失败(httpx.HTTPError 家族)→ BLOCKED,
    不计编译轮次、不扣返工预算(原实现异常向上传播 → 节点 raise → 图中断/API 任务死)。"""
    import httpx

    class Timeout:
        def health(self):
            return True

        def compile(self, code, project_name):
            raise httpx.TimeoutException("connect timeout")

    w = CompileWorker(llm=None, store=ArtifactStore(root=tmp_path),
                      compile_client=Timeout())
    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=3)
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    sub, msg = w.run(st, sub)
    assert "BLOCKED" in msg and "不可用" in msg
    assert st.rework_budget_left == 3   # 不扣预算
    assert sub.compile_errors == []     # 未计入编译轮次


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


def test_compile_experience_hits_reach_llm_fix_context(tmp_path):
    """经验库命中真实进入修复 LLM 上下文:每轮 _llm_fix 的 human 消息
    含 experience 附注(具体错误映射走动态检索,不在静态 skill 内容里)。"""
    from agents.kingdee_plugin_agent.graph.workers.w3_generate import CodeOutput

    class FakeExperience:
        def search_related(self, error_code, message, k=3):
            return [{"text": "[CS0103] 名称不存在(变量/方法拼写或作用域) 修复:核对元数据字段名/事件签名",
                     "score": 0.9, "metadata": {}}]

    class _CodeLLM:
        """无 bind_tools 的 fake:捕获每轮消息,返回改写后代码(触发写回重编)。"""

        def __init__(self):
            self.seen = []

        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages):
            self.seen.append(messages)
            return CodeOutput(code="class X { /* w5 fixed */ }")

    llm = _CodeLLM()
    w = CompileWorker(llm=llm, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient(fail_first=99),
                      experience=FakeExperience())
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    sub, msg = w.run(st, sub)
    assert len(llm.seen) == MAX_COMPILE_ROUNDS           # 每失败轮 LLM 都收到 context
    for messages in llm.seen:
        human = next(m.content for m in messages if getattr(m, "type", "") == "human")
        assert "[CS0103] 名称不存在" in human            # 经验库命中随 context 注入修复 prompt


def test_smoke_ok_no_budget_change(tmp_path):
    class FakeSmoke:
        def deploy_and_verify(self, dll_path, form_id):
            assert form_id == "SAL_SaleOrder"   # 环境里取 form_id
            assert str(dll_path).endswith("Plugin.dll")  # 验证对象是 DLL 非源码
            return SmokeResult(ok=True, detail="assembly 加载 + 映射验证通过")

    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=3)
    st.environment = {"form_id": "SAL_SaleOrder"}
    w = SmokeWorker(llm=None, store=ArtifactStore(root=tmp_path), smoke_client=FakeSmoke())
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    _write_dll(tmp_path, sub)
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
    _write_code(tmp_path, sub)
    _write_dll(tmp_path, sub)
    sub, msg = w.run(st, sub)
    assert "STATUS: BLOCKED" in msg
    assert st.rework_budget_left == 2  # 冒烟失败扣 1 预算


# ── 任务指标计数(设计 §9/§12:pass-rate / 返工轮次 / 冒烟通过率随 State 统计)──

def test_metrics_compile_pass_count_on_success(tmp_path):
    """w5 编译通过 → compile_pass_count +1(fake 客户端)。"""
    w = CompileWorker(llm=None, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient())
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    sub, msg = w.run(st, sub)
    assert st.metrics["compile_pass_count"] == 1
    assert st.metrics["compile_fail_count"] == 0


def test_metrics_compile_fail_count_on_exhaust(tmp_path):
    """w5 编译 5 轮超限 → compile_fail_count +1。"""
    w = CompileWorker(llm=None, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient(fail_first=99))
    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=3)
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    sub, msg = w.run(st, sub)
    assert st.metrics["compile_fail_count"] == 1
    assert st.metrics["compile_pass_count"] == 0


def test_metrics_smoke_pass_and_fail_counts(tmp_path):
    """w5_5 冒烟结果计数:通过 → smoke_pass_count;失败 → smoke_fail_count。"""

    class FakeOk:
        def deploy_and_verify(self, dll_path, form_id):
            return SmokeResult(ok=True, detail="ok")

    class FakeBad:
        def deploy_and_verify(self, dll_path, form_id):
            return SmokeResult(ok=False, detail="FormId 映射缺失")

    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=3)
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    _write_dll(tmp_path, sub)
    sub, msg = SmokeWorker(llm=None, store=ArtifactStore(root=tmp_path),
                           smoke_client=FakeOk()).run(st, sub)
    assert st.metrics["smoke_pass_count"] == 1
    assert st.metrics["smoke_fail_count"] == 0

    st2 = TaskState(requirement_spec={}, todo=[], rework_budget_left=3)
    sub2 = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub2)
    _write_dll(tmp_path, sub2)
    sub2, msg = SmokeWorker(llm=None, store=ArtifactStore(root=tmp_path),
                            smoke_client=FakeBad()).run(st2, sub2)
    assert st2.metrics["smoke_fail_count"] == 1
    assert st2.metrics["smoke_pass_count"] == 0


def test_metrics_defaults_all_zero():
    """metrics 缺省全 0(METRIC_KEYS 五项)。"""
    st = TaskState(requirement_spec={}, todo=[])
    assert st.metrics == {"compile_pass_count": 0, "compile_fail_count": 0,
                          "rework_rounds": 0, "smoke_pass_count": 0,
                          "smoke_fail_count": 0}


# ── 冒烟链路结构级修复:编译产物 DLL 传递 + 无 DLL 跳过验证 ────────────────

def test_w5_stores_dll_path_on_success(tmp_path):
    """w5 编译通过:后端产出 dll_path → subtask.dll_path(mock 后端为空 → 不设)。"""

    class WithDll:
        def health(self):
            return True

        def compile(self, code, project_name):
            return CompileResult(success=True, raw_output="", duration_ms=0,
                                 errors=[], dll_path=str(tmp_path / "out" / "Plugin.dll"))

    w = CompileWorker(llm=None, store=ArtifactStore(root=tmp_path),
                      compile_client=WithDll())
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    sub, msg = w.run(st, sub)
    assert "STATUS: DONE" in msg
    assert sub.dll_path.endswith("Plugin.dll")

    # mock 后端(dll_path 缺省空)→ 不设 dll_path
    sub2 = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub2)
    sub2, msg = CompileWorker(llm=None, store=ArtifactStore(root=tmp_path),
                              compile_client=FakeCompileClient()).run(
        TaskState(requirement_spec={}, todo=[]), sub2)
    assert sub2.dll_path == ""


def test_smoke_skips_without_dll(tmp_path):
    """无 DLL(编译后端未产出)→ 跳过部署验证:DONE_WITH_CONCERNS 显式标注,
    不扣预算、不计冒烟指标 —— 不再拿源码 Plugin.cs 冒充 DLL 去验证。"""

    class FakeSmoke:
        def deploy_and_verify(self, dll_path, form_id):
            raise AssertionError("无 DLL 时不应调用冒烟客户端")

    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=3)
    w = SmokeWorker(llm=None, store=ArtifactStore(root=tmp_path), smoke_client=FakeSmoke())
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)   # 只有源码,无 dll_path
    sub, msg = w.run(st, sub)
    assert "STATUS: DONE_WITH_CONCERNS" in msg
    assert "无 DLL" in msg and "跳过部署验证" in msg
    assert st.rework_budget_left == 3                     # 不扣预算
    assert st.metrics["smoke_pass_count"] == 0            # 跳过不是冒烟结果,不计指标
    assert st.metrics["smoke_fail_count"] == 0


def test_smoke_receives_dll_path_from_compile(tmp_path):
    """fake 后端产出 dll_path → w5 存 subtask.dll_path → w5.5 冒烟收到该路径。"""

    class FakeSmoke:
        def __init__(self):
            self.seen = []

        def deploy_and_verify(self, dll_path, form_id):
            self.seen.append(str(dll_path))
            return SmokeResult(ok=True, detail="ok")

    fake = FakeSmoke()
    w5 = CompileWorker(llm=None, store=ArtifactStore(root=tmp_path),
                       compile_client=FakeCompileClient())
    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=3)
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    sub, _ = w5.run(st, sub)
    # mock 编译后端无 DLL → w5.5 跳过;手工注入 dll_path 验证传递契约
    sub.dll_path = str(tmp_path / "A1" / "Plugin.dll")
    (tmp_path / "A1" / "Plugin.dll").write_bytes(b"PE\x00\x00")
    sub, msg = SmokeWorker(llm=None, store=ArtifactStore(root=tmp_path),
                           smoke_client=fake).run(st, sub)
    assert fake.seen == [str(tmp_path / "A1" / "Plugin.dll")]
    assert "STATUS: DONE" in msg


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


def test_package_worker_includes_dll_when_present(tmp_path):
    """w6 打包:w5 产出 DLL 时入包 bin/Plugin.dll(冒烟链路结构级修复)。"""
    import zipfile
    w = PackageWorker(llm=None, store=ArtifactStore(root=tmp_path), builder=None,
                      output_dir=tmp_path)
    st = TaskState(requirement_spec={}, todo=[Subtask("A1", "bill", "x", [], "packaged")])
    (tmp_path / "A1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "A1" / "Plugin.cs").write_text("class X {}", encoding="utf-8")
    (tmp_path / "A1" / "Plugin.dll").write_bytes(b"PE\x00\x00")
    sub = Subtask("A1", "bill", "x", [], "packaged")
    sub.dll_path = str(tmp_path / "A1" / "Plugin.dll")
    sub, msg = w.run(st, sub)
    with zipfile.ZipFile(st.final_deliverable) as z:
        assert "bin/Plugin.dll" in z.namelist()
        assert z.read("bin/Plugin.dll") == b"PE\x00\x00"
    # 无 DLL(mock 后端)→ 包内无 bin/ 条目(打包器容忍,现有契约不破坏)
    st2 = TaskState(requirement_spec={}, todo=[])
    sub2 = Subtask("A1", "bill", "x", [], "packaged")
    sub2, _ = PackageWorker(llm=None, store=ArtifactStore(root=tmp_path),
                            builder=None, output_dir=tmp_path).run(st2, sub2)
    with zipfile.ZipFile(st2.final_deliverable) as z:
        assert "bin/Plugin.dll" not in z.namelist()
        assert "source/Plugin.cs" in z.namelist()


def test_package_worker_stamps_spec_version(tmp_path):
    """w6 打包把冻结版本 + spec 快照盖进交付包 records/spec.json(需求版本冻结可审计)。"""
    import zipfile
    w = PackageWorker(llm=None, store=ArtifactStore(root=tmp_path), builder=None,
                      output_dir=tmp_path)
    st = TaskState(requirement_spec={"requirement": "审核校验", "decisions": []},
                   todo=[Subtask("A1", "bill", "x", [], "packaged")], spec_version=1)
    st.todo[0].code_path = str(tmp_path / "A1" / "Plugin.cs")
    (tmp_path / "A1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "A1" / "Plugin.cs").write_text("class X {}", encoding="utf-8")
    sub = Subtask("A1", "bill", "x", [], "packaged")
    sub, msg = w.run(st, sub)
    with zipfile.ZipFile(st.final_deliverable) as z:
        record = _json.loads(z.read("records/spec.json"))
    assert record["spec_version"] == 1
    assert record["requirement_spec"]["requirement"] == "审核校验"


def test_package_worker_records_design_and_review(tmp_path):
    """交付包 records 接线(设计 §5.4/§12):design.md + review.json(含 Minor)进包。

    原实现 deliverable 只有 {code, dll_path, subtask_id},records/design.json 与
    records/review.json 恒为空 {};修复后从产物库读入,Minor 意见随包可审计。
    """
    import zipfile
    w = PackageWorker(llm=None, store=ArtifactStore(root=tmp_path), builder=None,
                      output_dir=tmp_path)
    st = TaskState(requirement_spec={}, todo=[Subtask("A1", "bill", "x", [], "packaged")])
    (tmp_path / "A1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "A1" / "Plugin.cs").write_text("class X {}", encoding="utf-8")
    (tmp_path / "A1" / "design.md").write_text("# 设计:A1 库存校验\n数量 > 0 拦截",
                                               encoding="utf-8")
    (tmp_path / "A1" / "review.json").write_text(_json.dumps([
        {"severity": "Minor", "line": 3, "issue": "命名建议", "依据": "规范",
         "修法": "改标识符"},
        {"severity": "Critical", "line": 1, "issue": "缺校验", "依据": "s", "修法": "r"},
    ]), encoding="utf-8")
    sub = Subtask("A1", "bill", "x", [], "packaged")
    sub, msg = w.run(st, sub)
    with zipfile.ZipFile(st.final_deliverable) as z:
        design = _json.loads(z.read("records/design.json"))
        review = _json.loads(z.read("records/review.json"))
    assert design["content"] and "库存校验" in design["content"]   # 设计正文进包
    assert review[0]["severity"] == "Minor"                        # Minor 意见在包
    assert review[0]["issue"] == "命名建议"
    assert review[1]["severity"] == "Critical"


def test_package_worker_records_missing_artifacts_tolerated(tmp_path):
    """记录缺失容错:design.md / review.json 未落盘 → 包仍可产出(记录为空占位)。"""
    import zipfile
    w = PackageWorker(llm=None, store=ArtifactStore(root=tmp_path), builder=None,
                      output_dir=tmp_path)
    st = TaskState(requirement_spec={}, todo=[Subtask("A1", "bill", "x", [], "packaged")])
    (tmp_path / "A1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "A1" / "Plugin.cs").write_text("class X {}", encoding="utf-8")
    sub = Subtask("A1", "bill", "x", [], "packaged")
    sub, msg = w.run(st, sub)
    with zipfile.ZipFile(st.final_deliverable) as z:
        design = _json.loads(z.read("records/design.json"))
        review = _json.loads(z.read("records/review.json"))
    assert design == {} and review == {}


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
    assert r["spec_version"] == 1                # 需求确认即冻结,版本盖章
    # spec 落盘为 JSON(终审 C5:非 repr)
    spec = _json.loads((tmp_path / "requirement" / "spec.json").read_text(encoding="utf-8"))
    assert spec["decisions"][0]["a"] == "SAL_SaleOrder"
    # 冒烟链路(结构级修复):确认时从 decisions 提取 FormId → state.environment
    assert r["environment"]["form_id"] == "SAL_SaleOrder"


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


def test_graph_subtask_max_rework_fails(tmp_path):
    """子任务上限(设计 §5.1 上限字段):max_rework=1 → 第 2 次审查退回时
    failed(不再 needs_rework);全局预算仍按实际返工轮次扣减(2 轮 → 剩 1)。"""
    llm = ScriptedLLM(scripts={
        QuestionsOutput: [{"questions": ["FormId?"]}],
        PlanOutput: [{"subtasks": [{"id": "A1", "plugin_type": "bill", "title": "x",
                                    "deps": [], "acceptance_criteria": "审核拦截生效",
                                    "max_rework": 1}]}],
        DesignOutput: [{"design_markdown": "# 设计"}],
        CodeOutput: [{"code": "class A1 {}"}, {"code": "class A1 { /* 重写1 */ }"},
                     {"code": "class A1 { /* 重写2 */ }"}],
        ReviewOutput: [{"findings": [{"severity": "Critical", "issue": "缺校验"}]},
                       {"findings": [{"severity": "Critical", "issue": "仍缺校验"}]}],
    }, default={"action": "run"})
    app = build_graph(llm=llm, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient(), smoke_client=_OkSmoke(),
                      output_dir=tmp_path)
    cfg = _cfg("maxrw")
    r = app.invoke({"requirement_spec": {"requirement": "x"}, "todo": []}, cfg)
    r = app.invoke(Command(resume="SAL_SaleOrder"), cfg)
    r = app.invoke(Command(resume="确认"), cfg)
    assert r["action"].startswith("fail")
    assert _status_map(r["todo"]) == {"A1": "failed"}
    assert r["rework_budget_left"] == 1          # 2 次返工轮次已消耗(全局预算照扣)
    a1 = next(t for t in r["todo"] if t.id == "A1")
    assert a1.rework_count == 2
    assert a1.max_rework == 1
    assert a1.acceptance_criteria == "审核拦截生效"


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
    spec_before = r["requirement_spec"]
    r = app.invoke(Command(resume="按 0 校验"), cfg)
    assert r["user_feedback"] == ["按 0 校验"]
    # 需求版本冻结:确认后中途回答只记反馈,不改 requirement_spec(快照前后一致)
    assert r["requirement_spec"] == spec_before
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


# ── 失败收尾"未完成"包(设计 §8:部分产物 + 退回意见 + 原因)──────────────

def test_graph_budget_exhausted_produces_failed_package(tmp_path):
    """返工预算耗尽 fail → 失败打包节点产出 deliverable-failed-*.zip:
    records/status.json 含原因 + spec_version;compile_errors(编译超限 5 轮
    后的错误日志)与已有产物(设计/代码/审查记录)逐子任务进包。"""
    import zipfile
    store = ArtifactStore(root=tmp_path)
    sub = Subtask("A1", "bill", "x", [], "needs_rework")
    sub.compile_errors = [{"code": "CS0103", "message": "名称 m 不存在"}]
    (tmp_path / "A1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "A1" / "Plugin.cs").write_text("class A1 {}", encoding="utf-8")
    (tmp_path / "A1" / "design.md").write_text("# 设计:A1 库存校验", encoding="utf-8")
    (tmp_path / "A1" / "review.json").write_text(_json.dumps([
        {"severity": "Minor", "line": 2, "issue": "命名建议", "依据": "s", "修法": "r"}]),
        encoding="utf-8")
    app = build_graph(llm=None, store=store, output_dir=tmp_path)
    r = app.invoke({"requirement_spec": {"requirement": "x"},
                    "todo": [Subtask("B1", "service", "y", [], "pending"), sub],
                    "rework_budget_left": 0}, _cfg("failpkg", todo_count=2))
    assert r["action"] == "fail:返工预算耗尽"
    assert _status_map(r["todo"]) == {"A1": "failed", "B1": "failed"}
    pkg = r["final_deliverable"]
    assert "failed" in _Path(pkg).name                      # 文件名标注失败态
    with zipfile.ZipFile(pkg) as z:
        status = _json.loads(z.read("records/status.json"))
        errs = _json.loads(z.read("subtasks/A1/compile_errors.json"))
        code = z.read("subtasks/A1/source/Plugin.cs").decode("utf-8")
        design = z.read("subtasks/A1/design.md").decode("utf-8")
        review = _json.loads(z.read("subtasks/A1/review.json"))
    assert status["reason"] == "fail:返工预算耗尽"           # 原因进包
    assert status["spec_version"] == 1
    assert errs[0]["code"] == "CS0103"                      # 编译超限错误日志进包
    assert "class A1" in code and "库存校验" in design       # 部分产物进包
    assert review[0]["severity"] == "Minor"                 # 退回意见(含 Minor)进包
    assert r["final_deliverables"] == [pkg]


def test_graph_time_budget_exhausted_produces_failed_package(tmp_path):
    """时间预算耗尽 fail(30min 总闸)→ 同样走失败打包(未完成包),原因可审计。"""
    import time as _t
    import zipfile
    app = build_graph(llm=None, store=ArtifactStore(root=tmp_path), output_dir=tmp_path)
    r = app.invoke({"requirement_spec": {"requirement": "x"},
                    "todo": [Subtask("A1", "bill", "x", [], "pending")],
                    "started_at": _t.time() - 2000}, _cfg("timefail"))
    assert r["action"] == "fail:时间预算耗尽"
    with zipfile.ZipFile(r["final_deliverable"]) as z:
        status = _json.loads(z.read("records/status.json"))
    assert status["reason"] == "fail:时间预算耗尽"


def test_build_failed_sanitizes_subtask_ids(tmp_path):
    """评审 Minor:失败包 zip 条目 id 净化 —— 非法 id(路径穿越字符)替换为 "_",
    空 id 兜底 "unknown";产物保留且无 "../" 穿越条目。"""
    import zipfile
    from agents.kingdee_plugin_agent.tools.package import PackageBuilder
    builder = PackageBuilder(output_dir=tmp_path)
    p = builder.build_failed([
        {"id": "A1", "status": "failed", "code": "class A1 {}"},
        {"id": "../evil", "status": "failed", "code": "class Evil {}"},
        {"id": "B1/x", "status": "failed", "code": "class B {}"},
        {"id": "", "status": "failed", "code": "class C {}"},
    ], reason="fail:测试")
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
    assert "subtasks/A1/source/Plugin.cs" in names       # 合法 id 原样
    assert "subtasks/___evil/source/Plugin.cs" in names  # "../evil" → "___evil"
    assert "subtasks/B1_x/source/Plugin.cs" in names     # "B1/x" → "B1_x"
    assert "subtasks/unknown/source/Plugin.cs" in names  # 空 id → "unknown"
    assert all(".." not in n for n in names)             # 无路径穿越条目
    assert all(not n.startswith("subtasks/../") for n in names)


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


# ── 时间预算(设计 §8 全流程 30min 总闸)──────────────────────────────

def test_task_state_time_and_version_defaults():
    """started_at 缺省 0.0(未设置,不触发预算判定);spec_version 缺省 1。"""
    st = TaskState(requirement_spec={}, todo=[])
    assert st.started_at == 0.0
    assert st.spec_version == 1


def test_supervisor_decide_time_budget_exceeded():
    """全流程时间预算总闸:started_at 距今 >1800s 且有未交付工作 → fail:时间预算耗尽,
    剩余子任务标记 failed(与返工预算同语义)。"""
    import time as _t
    s = Supervisor(llm=None, workers={})
    st = TaskState(requirement_spec={}, todo=[Subtask("A1", "bill", "x", [], "pending")],
                   started_at=_t.time() - 2000)
    assert s.decide(st) == "fail:时间预算耗尽"
    assert st.todo[0].status == "failed"


def test_supervisor_decide_zero_started_at_normal():
    """started_at=0.0(未设置/旧状态兼容)不触发时间预算判定,正常派发。"""
    s = Supervisor(llm=None, workers={})
    st = TaskState(requirement_spec={}, todo=[Subtask("A1", "bill", "x", [], "pending")])
    assert s.decide(st) == "run:A1"


def test_supervisor_llm_context_includes_time_budget():
    """LLM 决策上下文含时间预算:摘要表带已用/总闸时长,LLM 可选择 fail。"""
    import time as _t
    s = Supervisor(llm=None, workers={})
    st = TaskState(requirement_spec={}, todo=[], started_at=_t.time() - 100)
    table = s._summary_table(st)
    assert "时间预算" in table and "总闸 1800s" in table


def test_supervisor_llm_finish_with_empty_todo_falls_back():
    """终审 Minor:澄清期(todo 空)LLM 幻觉 finish → 不提前结束图,回落确定性兜底。

    原实现 `a == "finish" → return "finish"`:零交付结束图(CLI 误报成功)。
    """
    from agents.kingdee_plugin_agent.graph.supervisor import DecideAction

    class HallucinateFinish:
        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            return DecideAction(action="finish")

    s = Supervisor(llm=HallucinateFinish(), workers={})
    st = TaskState(requirement_spec={}, todo=[])           # 澄清期:无子任务
    assert s.decide(st) == "ask_user"                      # 回落兜底,非 finish
    # 全部 delivered 时 LLM finish 依旧放行(第 2 步确定性已拦截,双保险)
    st2 = TaskState(requirement_spec={}, todo=[Subtask("A1", "bill", "x", [], "delivered")])
    assert s.decide(st2) == "finish"


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


# ── 任务指标随 State 统计(设计 §9/§12)───────────────────────────────

def test_graph_metrics_full_flow_with_rework(tmp_path):
    """图全链路指标:编译通过计数 + w4 Needs fixes 返工 1 轮 → rework_rounds=1。

    mock 编译后端无 DLL 产出 → w5.5 跳过部署验证(不计冒烟指标,冒烟通过率
    只统计真实验证结果;接真实 msbuild 后端后 smoke_pass_count 恢复计数)。"""
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
    cfg = _cfg("metrics")
    r = app.invoke({"requirement_spec": {"requirement": "x"}, "todo": []}, cfg)
    r = app.invoke(Command(resume="SAL_SaleOrder"), cfg)
    r = app.invoke(Command(resume="确认"), cfg)
    assert r["action"] == "finish"
    m = r["metrics"]
    assert m["compile_pass_count"] == 1
    assert m["smoke_pass_count"] == 0         # mock 后端无 DLL → 冒烟跳过,不计数
    assert m["rework_rounds"] == 1            # w4 Needs fixes → 返工 1 轮(预算扣 1 同源)
    assert m["compile_fail_count"] == 0
    assert m["smoke_fail_count"] == 0


def test_graph_metrics_parallel_merge_no_double_count(tmp_path):
    """并行指标合并:两个独立子任务同轮编译 → 计数 = 2(增量 reducer 求和,
    跨多轮派发不重复累计)。冒烟指标:mock 后端无 DLL → 两任务均跳过,不计。"""
    app = build_graph(llm=None, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient(), smoke_client=_OkSmoke(),
                      output_dir=tmp_path)
    cfg = _cfg("parmetrics", todo_count=2)
    final = None
    for chunk in app.stream({"requirement_spec": {"requirement": "复合需求"},
                             "todo": [Subtask("A1", "bill", "a"),
                                      Subtask("B1", "service", "b")]},
                            cfg, stream_mode="values"):
        final = chunk
    assert final["action"] == "finish"
    m = final["metrics"]
    assert m["compile_pass_count"] == 2       # 并行两个子任务各自 +1
    assert m["smoke_pass_count"] == 0         # 无 DLL → 跳过,不计
    assert m["rework_rounds"] == 0


# ── OTel span(设计 §12:主管派发 / worker 状态变迁 / 编译轮次打 trace)────

class _RecordingSpan:
    """fake span:记录 set_attribute 键值(等价 no-op tracer 的可用子集)。"""

    def __init__(self, name):
        self.name = name
        self.attrs = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def set_attribute(self, key, value):
        self.attrs[key] = value


class _RecordingTracer:
    """fake tracer:start_as_current_span 返回记录 span —— 无需真实 collector。"""

    def __init__(self):
        self.spans = []

    def start_as_current_span(self, name, **kwargs):
        span = _RecordingSpan(name)
        self.spans.append(span)
        return span


def test_otel_spans_wired_without_collector(tmp_path, monkeypatch):
    """可观测(设计 §12):worker 状态迁移 / 编译轮次 / 主管决策各打 span;
    无 collector(no-op tracer)环境不崩 —— fake tracer 记录 span 名与低基数属性。"""
    import agents.kingdee_plugin_agent.graph.workers.base as _base_mod
    import agents.kingdee_plugin_agent.graph.workers.w5_compile as _w5_mod
    import agents.kingdee_plugin_agent.graph.supervisor as _sup_mod

    rec = _RecordingTracer()
    monkeypatch.setattr(_base_mod, "get_tracer", lambda: rec)
    monkeypatch.setattr(_w5_mod, "get_tracer", lambda: rec)
    monkeypatch.setattr(_sup_mod, "get_tracer", lambda: rec)

    # worker 状态迁移 span + 编译轮次 span(fail_first=1 → 2 轮)
    w = CompileWorker(llm=None, store=ArtifactStore(root=tmp_path),
                      compile_client=FakeCompileClient(fail_first=1))
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    _write_code(tmp_path, sub)
    sub, msg = w.run(TaskState(requirement_spec={}, todo=[]), sub)
    names = [s.name for s in rec.spans]
    assert names.count("kingdee.w5.compile_round") == 2      # 每轮编译一个 span
    worker_span = next(s for s in rec.spans if s.name == "kingdee.worker.w5")
    assert worker_span.attrs["subtask_id"] == "A1"
    assert worker_span.attrs["plugin_type"] == "bill"
    assert worker_span.attrs["status"] == "DONE"
    round_span = next(s for s in rec.spans if s.name == "kingdee.w5.compile_round")
    assert round_span.attrs["round"] == 1
    assert round_span.attrs["success"] is False

    # 主管决策 span(action 低基数属性)
    s = Supervisor(llm=None, workers={})
    st = TaskState(requirement_spec={}, todo=[Subtask("A1", "bill", "x", [], "delivered")])
    assert s.decide(st) == "finish"
    decide_span = next(s for s in rec.spans if s.name == "kingdee.supervisor.decide")
    assert decide_span.attrs["action"] == "finish"

    # OBS-CORE-003(评审 Important):ask_user:<问题> 的问题文本是用户/LLM 生成的
    # 高基数自由文本 —— span 只记动作类型,任何 span 属性都不得含问题原文
    class AskLLM:
        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            return DecideAction(action="ask_user", question="校验规则确认?")

    s_ask = Supervisor(llm=AskLLM(), workers={})
    st_ask = TaskState(requirement_spec={}, todo=[])
    assert s_ask.decide(st_ask) == "ask_user:校验规则确认?"
    ask_spans = [sp for sp in rec.spans if sp.name == "kingdee.supervisor.decide"
                 and sp.attrs.get("action") == "ask_user"]
    assert ask_spans                                   # 动作类型保留
    for sp in rec.spans:
        for v in sp.attrs.values():
            assert "校验规则确认?" not in str(v)       # 无用户派生文本进 span


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


def test_w1_record_answer_and_build_spec(tmp_path):
    """w1 澄清接口:record_answer 记录 + build_spec 组装 decisions(确认摘要的基础)。

    原 interrupt_message 方法为死代码已删除(agent.py w1 节点用内联 payload
    dict:type/round/text 与 type/confirm/summary,API/CLI 按其契约分支)。
    """
    w = RequirementWorker(llm=None, store=ArtifactStore(root=tmp_path))
    st = TaskState(requirement_spec={"requirement": "审核校验插件"}, todo=[],
                   clarify_questions=["Q1", "Q2"])
    w.record_answer(st, "A1")
    w.record_answer(st, "A2")
    assert st.clarify_answers == ["A1", "A2"]
    spec = w.build_spec(st)
    assert spec["decisions"] == [{"q": "Q1", "a": "A1"}, {"q": "Q2", "a": "A2"}]
    assert "需求确认摘要" in build_confirmation_summary(spec)   # 确认摘要含决策


def test_agent_name_and_recursion_formula():
    assert AGENT_NAME == "kingdee_plugin_agent"
    assert default_recursion_limit(0) == 100
    assert default_recursion_limit(3) == 160
    # 终审 Important:调用点(CLI/API)澄清期固定按 n=10 给足,须 ≥250 防
    # 复合任务(8 阶段 × ceil(n/3) 并行 × 返工重跑)溢出 → GraphRecursionError
    assert default_recursion_limit(10) == 300
    assert default_recursion_limit(10) >= 250


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


def test_cli_env_recorded_in_initial_state(tmp_path, monkeypatch):
    """--env 消费(最小化):env 值进初始 state.environment["env_name"](节点可感知)。"""
    monkeypatch.setenv("KD_BASE_URL", "http://kd-test:8080")
    captured = {}
    real = build_graph(llm=None, store=ArtifactStore(root=tmp_path),
                       compile_client=FakeCompileClient(), smoke_client=_OkSmoke(),
                       output_dir=tmp_path)

    class _SpyGraph:
        """记录首次 invoke 的初始 state(后续 resume 是 Command,只认 dict)。"""

        def invoke(self, state, cfg):
            if isinstance(state, dict) and "state" not in captured:
                captured["state"] = state
            return real.invoke(state, cfg)

    monkeypatch.setattr(_cli, "build_graph", lambda: _SpyGraph())
    answers = iter(["SAL_SaleOrder", "确认"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert run_cli(["给采购单审核加库存校验", "--env", "prod"]) == 0
    assert captured["state"]["environment"] == {"env_name": "prod"}

# ═══════════════════════════ C12 Web API(apikey + SSE + 澄清/验收)═══════════════════════════
import time as _time

from fastapi import HTTPException
from fastapi.testclient import TestClient

from agents.kingdee_plugin_agent.api import create_app

_KD_ENV = {"KD_BASE_URL": "http://kd-test:8080", "KD_USERNAME": "u",
           "KD_PASSWORD": "p", "KD_DATA_CENTER": "dc"}
_HEADERS = {"X-API-Key": "k"}


def _set_kd_env(monkeypatch):
    """KD_* 4 项环境齐备(环境硬门槛需全量校验,C11 复审 carry-over)。"""
    for name, value in _KD_ENV.items():
        monkeypatch.setenv(name, value)


def _det_graph_factory(tmp_path):
    """确定性图工厂:llm=None + fake 编译/冒烟/打包(与 C11 CLI 同一注入思路)。"""
    return build_graph(llm=None, store=ArtifactStore(root=tmp_path),
                       compile_client=FakeCompileClient(), smoke_client=_OkSmoke(),
                       output_dir=tmp_path)


def _create_task(client, tmp_path, requirement="给采购单审核加库存校验"):
    """建任务(环境齐备 + 确定性图)→ 返回 task_id。"""
    r = client.post("/tasks", json={"requirement": requirement, "env": "test"},
                    headers=_HEADERS)
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


def _wait_state(client, task_id, pred, timeout=15):
    """轮询 /state 直到 pred(快照)成立;超时抛错。"""
    deadline = _time.time() + timeout
    last = None
    while _time.time() < deadline:
        last = client.get(f"/tasks/{task_id}/state", headers=_HEADERS).json()
        if pred(last):
            return last
        _time.sleep(0.05)
    raise AssertionError(f"state 超时未满足({timeout}s): {last}")


def _run_to_done(client, task_id, timeout=30):
    """答澄清问题 + 确认 → 确定性全流程跑完 → 终态快照(done)。"""
    for ans in ("SAL_SaleOrder", "确认"):
        r = client.post(f"/tasks/{task_id}/answers", json={"answer": ans},
                        headers=_HEADERS)
        assert r.status_code == 200, r.text
    return _wait_state(client, task_id, lambda s: s["done"], timeout=timeout)


def _sse_all(client, task_id, timeout=15):
    """连接 SSE 读到流结束(任务完成 → 服务端自动关闭);返回全部事件。

    注:本仓库 starlette 1.4.1 的 TestClient 传输为缓冲式(handle_request 等 app
    完成后才返回响应),故只在任务结束后连 SSE 读全量回放 —— 与断线重连语义一致;
    实时推送路径由 test_task_handle_live_push_and_close 单元覆盖。
    """
    out = []
    with client.stream("GET", f"/tasks/{task_id}/events", headers=_HEADERS) as resp:
        assert resp.status_code == 200
        buf, deadline = "", _time.time() + timeout
        it = resp.iter_text()
        while _time.time() < deadline:
            try:
                buf += next(it)
            except StopIteration:
                break
        buf = buf.replace("\r\n", "\n")     # SSE 行分隔是 CRLF,先归一化
        while "\n\n" in buf:
            raw, buf = buf.split("\n\n", 1)
            evt = {}
            for line in raw.split("\n"):
                line = line.strip()
                if line.startswith("event:"):
                    evt["event"] = line[6:].strip()
                elif line.startswith("data:"):
                    evt["data"] = line[5:].strip()
            if evt:
                evt["data"] = _json.loads(evt["data"])
                out.append(evt)
    assert out, "SSE 无事件"
    return out


def test_api_requires_apikey():
    """无 apikey(且未配置)→ 401:默认拒绝。"""
    client = TestClient(create_app())
    r = client.post("/tasks", json={"requirement": "x", "env": "test"})
    assert r.status_code == 401


def test_api_cors_preflight():
    """CORS:跨域 OPTIONS 预检返回 CORS 头(演示页 web/kingdee-demo.html 可跨域的前提)。

    演示页从 :8080 静态服务 / file:// 访问 :8000 API,带 X-API-Key 头的请求触发
    preflight;CORSMiddleware 短路返回 200 + allow-origin/methods/headers。
    """
    client = TestClient(create_app(api_key="k"))
    r = client.options("/tasks", headers={
        "Origin": "http://127.0.0.1:8080",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-api-key,content-type",
    })
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*"
    assert "POST" in r.headers.get("access-control-allow-methods", "")
    assert "x-api-key" in r.headers.get("access-control-allow-headers", "").lower()


def test_api_auth_then_task(monkeypatch):
    """正确 apikey 通过鉴权;环境未配置 → 503(非 500/401)。"""
    client = TestClient(create_app(api_key="k"))
    r = client.post("/tasks", json={"requirement": "x", "env": "test"},
                    headers=_HEADERS)
    assert r.status_code in (200, 503)


def test_acceptance_feed(monkeypatch):
    """未知任务验收 → 404(只有 POST 创建的任务存在)。"""
    client = TestClient(create_app(api_key="k"))
    r = client.post("/tasks/1/acceptance", json={"accepted": False, "reason": "逻辑不符"},
                    headers=_HEADERS)
    assert r.status_code == 404


def test_api_env_gate_lists_all_missing_vars(monkeypatch):
    """环境硬门槛:缺 KD_USERNAME/PASSWORD/DATA_CENTER 也要 503 并点明(C11 复审 carry-over)。"""
    monkeypatch.setenv("KD_BASE_URL", "http://kd-test:8080")   # 只给 1 项
    client = TestClient(create_app(api_key="k"))
    r = client.post("/tasks", json={"requirement": "x", "env": "test"},
                    headers=_HEADERS)
    assert r.status_code == 503
    detail = r.json()["detail"]
    for missing in ("KD_USERNAME", "KD_PASSWORD", "KD_DATA_CENTER"):
        assert missing in detail


def test_api_create_state_and_answers(tmp_path, monkeypatch):
    """建任务 → /state 可见澄清 interrupt → answers 恢复 → 全流程 done(确定性图)。"""
    _set_kd_env(monkeypatch)
    client = TestClient(create_app(api_key="k",
                                   graph_factory=lambda: _det_graph_factory(tmp_path)))
    tid = _create_task(client, tmp_path)
    st = _wait_state(client, tid, lambda s: s["interrupt"])
    assert st["interrupt"]["type"] == "question"
    assert st["status"] == "waiting"
    st = _run_to_done(client, tid)
    assert st["final_deliverables"]
    assert st["todo"][0]["status"] == "delivered"


def test_api_state_rejects_unknown_task():
    """未知任务:state/events/answers 全部明确 404(只有 POST 创建的任务存在)。"""
    client = TestClient(create_app(api_key="k"))
    assert client.get("/tasks/nope/state", headers=_HEADERS).status_code == 404
    assert client.get("/tasks/nope/events", headers=_HEADERS).status_code == 404
    assert client.post("/tasks/nope/answers", json={"answer": "x"},
                       headers=_HEADERS).status_code == 404


def test_api_answers_conflict_when_task_done(tmp_path, monkeypatch):
    """任务已结束再答题 → 409(不阻塞、不静默丢答案)。"""
    _set_kd_env(monkeypatch)
    client = TestClient(create_app(api_key="k",
                                   graph_factory=lambda: _det_graph_factory(tmp_path)))
    tid = _create_task(client, tmp_path)
    _run_to_done(client, tid)
    r = client.post(f"/tasks/{tid}/answers", json={"answer": "多余回答"},
                    headers=_HEADERS)
    assert r.status_code == 409


def test_api_task_creation_sets_started_at(tmp_path, monkeypatch):
    """建任务即打时间戳:started_at 入初始 state,驱动全流程时间预算总闸(设计 §8)。"""
    _set_kd_env(monkeypatch)
    client = TestClient(create_app(api_key="k",
                                   graph_factory=lambda: _det_graph_factory(tmp_path)))
    tid = _create_task(client, tmp_path)
    handle = client.app.state.tasks[tid]
    assert handle.state["started_at"] > 0


def test_api_initial_state_environment_has_env_name(tmp_path, monkeypatch):
    """--env 消费(API):env 值进初始 state.environment["env_name"](节点可感知)。"""
    _set_kd_env(monkeypatch)
    client = TestClient(create_app(api_key="k",
                                   graph_factory=lambda: _det_graph_factory(tmp_path)))
    tid = _create_task(client, tmp_path, requirement="x")
    handle = client.app.state.tasks[tid]
    assert handle.state["environment"] == {"env_name": "test"}


def test_api_answers_frozen_after_confirmation_non_ask_user():
    """需求版本冻结:确认后 answers 拒绝非 ask_user 类型的恢复输入(409)——
    防止任何可被解释为 spec 修改的路径(question/confirm 只出现在确认前)。"""
    from agents.kingdee_plugin_agent.api import TaskHandle
    handle = TaskHandle("t1", graph=object(), cfg={},
                        initial_state={"spec_confirmed": True,
                                       "requirement_spec": {"requirement": "x"},
                                       "todo": []})
    handle.waiting = True
    handle.interrupt = {"type": "question", "round": 0, "text": "x"}
    with pytest.raises(HTTPException) as ei:
        handle.deliver_answer("改成 Y")
    assert ei.value.status_code == 409
    assert "冻结" in ei.value.detail


def test_api_answers_ask_user_ok_after_confirmation():
    """确认后 ask_user(执行中问题)恢复仍可用:只记反馈,不改 spec。"""
    from agents.kingdee_plugin_agent.api import TaskHandle
    handle = TaskHandle("t1", graph=object(), cfg={},
                        initial_state={"spec_confirmed": True,
                                       "requirement_spec": {"requirement": "x"},
                                       "todo": []})
    handle.waiting = True
    handle.interrupt = {"type": "ask_user", "question": "补充什么?"}
    handle.deliver_answer("按 0 校验")
    assert handle._resume == "按 0 校验"


def test_api_sse_streams_progress(tmp_path, monkeypatch):
    """SSE:todo/interrupt/done 事件流 + 重放;任务结束后流自动关闭(可读到 EOF)。"""
    _set_kd_env(monkeypatch)
    client = TestClient(create_app(api_key="k",
                                   graph_factory=lambda: _det_graph_factory(tmp_path)))
    tid = _create_task(client, tmp_path)
    _run_to_done(client, tid)
    events = _sse_all(client, tid)
    kinds = [e["event"] for e in events]
    assert kinds[0] == "todo"                                     # 事件从 todo 开始
    assert "interrupt" in kinds
    assert events[kinds.index("interrupt")]["data"]["type"] == "question"
    assert kinds[-1] == "done"                                    # 流以 done 结束
    assert events[-1]["data"]["done"] is True
    assert events[-1]["data"]["todo"]                             # done 带全量快照


def test_task_handle_live_push_and_close():
    """SSE 实时推送路径(单元级):订阅回调即时收到图线程事件;done 后 sentinel 关闭。"""
    from agents.kingdee_plugin_agent.api import TaskHandle
    handle = TaskHandle("t", graph=None, cfg={}, initial_state={})
    got = []
    with handle._cond:
        handle._subscribers.append(got.append)
    handle._emit("todo", [])
    handle._set_interrupt({"type": "question", "text": "?"})
    assert [e["event"] for e in got] == ["todo", "interrupt"]
    handle._set_done()
    assert [e["event"] for e in got[:-1]] == ["todo", "interrupt", "done"]
    assert got[-1] is None   # sentinel:结束 SSE 流
    assert handle.snapshot()["status"] == "done"


def _acceptance_sig(reason: str) -> str:
    """验收拒绝条目签名(与 api.py record_acceptance 同规则:code|sha256(reason)[:12])。"""
    import hashlib as _hashlib
    return f"ARTIFACT|{_hashlib.sha256(reason.encode('utf-8')).hexdigest()[:12]}"


def _artifacts(rag) -> list[dict]:
    """经验库中全部验收拒绝条目(code=ARTIFACT)。"""
    return [h for h in rag.search("experience", "拒绝原因", k=50)
            if h["metadata"].get("code") == "ARTIFACT"]


def test_api_acceptance_reject_feeds_w7(tmp_path, monkeypatch):
    """拒绝 + 原因 → 真实 w7 经验库入库(proposed 态);接受不新增;验收结论可查。"""
    _set_kd_env(monkeypatch)
    from common.rag import ExperienceStore, RagClient
    rag = RagClient(data_dir=tmp_path / "rag")
    exp = ExperienceStore(rag)
    client = TestClient(create_app(api_key="k",
                                   graph_factory=lambda: _det_graph_factory(tmp_path),
                                   experience=exp))
    tid = _create_task(client, tmp_path)
    _run_to_done(client, tid)
    r = client.post(f"/tasks/{tid}/acceptance",
                    json={"accepted": False, "reason": "逻辑不符"}, headers=_HEADERS)
    assert r.status_code == 200
    hits = rag.search("experience", "逻辑不符", k=10,
                      filter={"signature": _acceptance_sig("逻辑不符")})
    assert len(hits) == 1
    assert hits[0]["metadata"]["status"] == "proposed"
    assert hits[0]["metadata"]["code"] == "ARTIFACT"
    r = client.post(f"/tasks/{tid}/acceptance", json={"accepted": True}, headers=_HEADERS)
    assert r.status_code == 200
    assert len(_artifacts(rag)) == 1            # 接受不新增沉淀
    st = client.get(f"/tasks/{tid}/state", headers=_HEADERS).json()
    assert st["acceptance"]["accepted"] is True # 最后结论(覆盖语义)


def test_api_acceptance_reject_distinct_reasons_accumulate(tmp_path, monkeypatch):
    """不同拒绝原因各自入库(签名 reason 感知),相同原因去重(复审 Important 修复)。

    旧实现签名恒为 "ARTIFACT|"(file_pattern 空),ExperienceStore 按签名去重会
    吞掉不同拒绝原因 —— 本测试用真实 ExperienceStore 验证累计与去重。
    """
    _set_kd_env(monkeypatch)
    from common.rag import ExperienceStore, RagClient
    rag = RagClient(data_dir=tmp_path / "rag")
    exp = ExperienceStore(rag)

    def _reject(reason: str) -> None:
        client = TestClient(create_app(api_key="k",
                                       graph_factory=lambda: _det_graph_factory(tmp_path),
                                       experience=exp))
        tid = _create_task(client, tmp_path)
        _run_to_done(client, tid)
        r = client.post(f"/tasks/{tid}/acceptance",
                        json={"accepted": False, "reason": reason}, headers=_HEADERS)
        assert r.status_code == 200

    _reject("逻辑不符")
    _reject("表单字段缺失")            # 不同原因 → 不同签名 → 各自入库
    _reject("逻辑不符")                # 相同原因 → 签名去重 → 不重复入库

    for reason in ("逻辑不符", "表单字段缺失"):
        hits = rag.search("experience", reason, k=10,
                          filter={"signature": _acceptance_sig(reason)})
        assert len(hits) == 1, (reason, hits)
    assert len(_artifacts(rag)) == 2    # 累计 2 条不同原因;重复原因已去重


# ── 反馈通道(设计 §12:部署后行为错误手动上报 → 经验库 DEPLOY 通道)────────

def _feedback_sig(reason: str) -> str:
    """反馈条目签名(与 api.py record_feedback 同规则:code|sha256(reason)[:12])。"""
    import hashlib as _hashlib
    return f"DEPLOY|{_hashlib.sha256(reason.encode('utf-8')).hexdigest()[:12]}"


def _deploy_entries(rag) -> list[dict]:
    """经验库中全部反馈条目(code=DEPLOY)。"""
    return [h for h in rag.search("experience", "反馈", k=50)
            if h["metadata"].get("code") == "DEPLOY"]


def test_api_feedback_unknown_task_404():
    """未知任务反馈 → 404(与验收/answers 同语义,只有 POST 创建的任务存在)。"""
    client = TestClient(create_app(api_key="k"))
    r = client.post("/tasks/nope/feedback", json={"reason": "单据保存后未刷新"},
                    headers=_HEADERS)
    assert r.status_code == 404
    # 无 apikey → 401(鉴权先于任务查找)
    r = client.post("/tasks/nope/feedback", json={"reason": "x"})
    assert r.status_code == 401


def test_api_feedback_feeds_experience(tmp_path, monkeypatch):
    """部署后行为错误上报 → 真实经验库 DEPLOY 通道入库(proposed 态);两个不同
    原因各自累计、相同原因去重;沉淀失败不阻塞反馈(never blocks)。"""
    _set_kd_env(monkeypatch)
    from common.rag import ExperienceStore, RagClient
    rag = RagClient(data_dir=tmp_path / "rag")
    exp = ExperienceStore(rag)
    client = TestClient(create_app(api_key="k",
                                   graph_factory=lambda: _det_graph_factory(tmp_path),
                                   experience=exp))
    tid = _create_task(client, tmp_path)

    def _feedback(reason: str) -> None:
        r = client.post(f"/tasks/{tid}/feedback", json={"reason": reason},
                        headers=_HEADERS)
        assert r.status_code == 200
        assert r.json()["feedback"]["reason"] == reason

    _feedback("单据保存后未刷新")
    _feedback("审核按钮不生效")        # 不同原因 → 不同签名 → 各自入库
    _feedback("单据保存后未刷新")      # 相同原因 → 签名去重 → 不重复入库
    for reason in ("单据保存后未刷新", "审核按钮不生效"):
        hits = rag.search("experience", reason, k=10,
                          filter={"signature": _feedback_sig(reason)})
        assert len(hits) == 1, (reason, hits)
        assert hits[0]["metadata"]["status"] == "proposed"
        assert hits[0]["metadata"]["code"] == "DEPLOY"
    assert len(_deploy_entries(rag)) == 2   # 累计 2 条不同原因;重复已去重


# ── load_skill 机制(skill 渐进式披露,对照 sentiment 模式)────────────────

def test_load_skill_returns_requirement_clarify():
    """load_skill("requirement-clarify") 返回 SKILL.md 全文 + 三套类型模板正文;
    未知 skill → error JSON 并列出可用项。"""
    import json
    from agents.kingdee_plugin_agent.skills.loader import load_skill

    payload = json.loads(load_skill.invoke({"skill_name": "requirement-clarify"}))
    assert payload["skill"] == "requirement-clarify"
    assert "金蝶插件需求澄清方法论" in payload["summary"]
    assert "一次一问" in payload["summary"]
    # references 是 name→content 映射:模板正文全量交付(LLM 无文件工具,不能只给文件名)
    assert set(payload["references"]) == {"bill.md", "list.md", "service.md"}
    assert "触发操作" in payload["references"]["bill.md"]
    assert "拦截方式" in payload["references"]["bill.md"]
    assert "服务入口" in payload["references"]["service.md"]
    assert "操作按钮" in payload["references"]["list.md"]
    assert "金蝶插件需求澄清方法论" in payload["content"]                   # SKILL.md 全文
    assert payload["scripts"] == []

    err = json.loads(load_skill.invoke({"skill_name": "nope"}))
    assert "error" in err
    assert "requirement-clarify" in err["available"]


def test_skill_summary():
    """skill_summary() 注入系统提示的摘要层:6 个 skill(w1 澄清 + 5 方法论)全在。"""
    import json
    from agents.kingdee_plugin_agent.skills.loader import skill_summary

    summary = json.loads(skill_summary())
    assert set(summary) == {"requirement-clarify", "design-builder",
                            "code-generator", "code-reviewer", "compile-fixer",
                            "knowledge-steward"}
    assert "多选优先" in summary["requirement-clarify"]
    assert "design.md" in summary["design-builder"]
    assert "template.cs" in summary["code-generator"]
    assert "Needs fixes" in summary["code-reviewer"]
    assert "MAX_COMPILE_ROUNDS" in summary["compile-fixer"] or "5 轮" in summary["compile-fixer"]
    assert "proposed" in summary["knowledge-steward"]     # 沉淀两态在摘要
    assert "bm25_weight" in summary["knowledge-steward"]  # 检索路由约定在摘要


def test_load_skill_all_six_skills():
    """6 个 skill 全可加载:content = SKILL.md 全文,references = name→content 映射。"""
    import json
    from agents.kingdee_plugin_agent.skills.loader import load_skill

    for name in ("requirement-clarify", "design-builder", "code-generator",
                 "code-reviewer", "compile-fixer", "knowledge-steward"):
        payload = json.loads(load_skill.invoke({"skill_name": name}))
        assert payload["skill"] == name
        assert "方法论" in payload["content"]          # SKILL.md 全文交付
        assert payload["references"], f"{name} references 不应为空"
        assert payload["scripts"] == []


def test_load_skill_design_builder_references():
    """design-builder references 含三类型完整检查清单(设计方法论关键内容)。"""
    import json
    from agents.kingdee_plugin_agent.skills.loader import load_skill

    payload = json.loads(load_skill.invoke({"skill_name": "design-builder"}))
    assert set(payload["references"]) == {"bill.md", "service.md", "list.md"}
    assert "事件绑定决策" in payload["content"]
    assert "验收自检" in payload["content"]
    assert "触发操作" in payload["references"]["bill.md"]
    assert "拦截方式" in payload["references"]["bill.md"]
    assert "事务边界" in payload["references"]["service.md"]
    assert "操作按钮" in payload["references"]["list.md"]
    assert "逐行" in payload["references"]["list.md"]     # 批量逐行处理语义


def test_load_skill_codegen_review_fixer_references():
    """code-generator/code-reviewer/compile-fixer references 关键方法论词断言。"""
    import json
    from agents.kingdee_plugin_agent.skills.loader import load_skill

    gen = json.loads(load_skill.invoke({"skill_name": "code-generator"}))
    assert "模板优先" in gen["content"]
    assert "AbstractBillPlugIn" in gen["references"]["bill.md"]
    assert "AbstractOperationServicePlugIn" in gen["references"]["service.md"]
    assert "AbstractListPlugIn" in gen["references"]["list.md"]
    assert "占位符" in gen["content"]

    rev = json.loads(load_skill.invoke({"skill_name": "code-reviewer"}))
    assert "Critical" in rev["content"]                    # 裁决规则
    assert "Needs fixes" in rev["content"]
    assert "AfterDoOperation" in rev["references"]["bill.md"]
    assert "回滚补偿" in rev["references"]["service.md"]

    fix = json.loads(load_skill.invoke({"skill_name": "compile-fixer"}))
    assert "5 轮" in fix["content"]
    # errors.md 纯方法论契约(具体错误映射不进 skill,单一来源经验库)
    assert "分类框架" in fix["references"]["errors.md"]
    assert "根因分析" in fix["references"]["errors.md"]
    assert "经验库" in fix["references"]["errors.md"]


def test_load_skill_knowledge_steward():
    """knowledge-steward 交付 SKILL.md 全文 + distillation/maintenance 两 references:
    沉淀质量标准(条目模板/好例坏例)、维护手册(种子增补/幂等)、
    检索路由速查表关键项(api_ref、bm25_weight 0.7 约定、分数方向警示)。"""
    import json
    from agents.kingdee_plugin_agent.skills.loader import load_skill

    payload = json.loads(load_skill.invoke({"skill_name": "knowledge-steward"}))
    assert payload["skill"] == "knowledge-steward"
    assert "方法论" in payload["content"]                        # SKILL.md 全文
    assert set(payload["references"]) == {"distillation.md", "maintenance.md"}
    assert payload["scripts"] == []

    # SKILL.md:检索路由速查表关键项(api_ref 库 + bm25_weight 0.7 约定 + 分数方向警示)
    assert "api_ref" in payload["content"]
    assert "bm25_weight" in payload["content"] and "0.7" in payload["content"]
    assert "L2" in payload["content"] and "RRF" in payload["content"]
    # 沉淀方法论关键点:proposed→verified、签名去重、不阻塞纪律、w7 无 LLM 绑定说明
    assert "proposed" in payload["content"] and "verified" in payload["content"]
    assert "code|file_pattern" in payload["content"]
    assert "不阻塞" in payload["content"]
    assert "无 LLM" in payload["content"]                        # w7 绑定决策文档化

    # distillation.md:条目模板(好例/坏例对比)+ 签名规则
    dist = payload["references"]["distillation.md"]
    assert "条目模板" in dist and "好例" in dist and "坏例" in dist
    assert "signature" in dist and "去重" in dist
    assert "proposed → verified" in dist

    # maintenance.md:维护四件套(种子增补/文档导入/规范库合并/review)
    maint = payload["references"]["maintenance.md"]
    assert "种子增补" in maint and "文档导入" in maint and "规范库合并" in maint
    assert "定期 review" in maint or "review" in maint
    assert "幂等" in maint


def test_seed_load_cli_main(tmp_path, capsys):
    """seed_load __main__ 入口(维护手册步骤 1.4 的命令真实可跑):
    main(["--data-dir", tmp]) 灌入种子并打印新增条数;二次运行幂等 0。"""
    from agents.kingdee_plugin_agent.seed.seed_load import main

    n1 = main(["--data-dir", str(tmp_path)])
    assert n1 >= 7                                # 7 条种子(含签名类 CS0506/CS0115)
    out = capsys.readouterr().out
    assert f"种子灌入完成:新增 {n1} 条" in out    # 打印契约(步骤 1.4 依赖的输出)
    n2 = main(["--data-dir", str(tmp_path)])
    assert n2 == 0                                # 幂等:签名已存在跳过


def test_errors_md_pure_methodology_no_static_mappings():
    """compile-fixer skill 纯方法论契约(errors.md + SKILL.md):分类框架/根因分析/
    检索策略/修复纪律在,不含任何静态 错误码 → 修法 映射 —— 具体映射单一来源为
    经验库(启动种子 seed/compile_errors.json + w7 沉淀),防静态表与动态库双份维护漂移。"""
    import json
    import re
    from agents.kingdee_plugin_agent.skills.loader import load_skill

    payload = json.loads(load_skill.invoke({"skill_name": "compile-fixer"}))
    errors_md = payload["references"]["errors.md"]
    # 方法论四件套
    assert "错误分类框架" in errors_md
    assert "根因分析方法" in errors_md
    assert "检索策略" in errors_md
    assert "修复纪律" in errors_md
    # 具体映射单一来源指向经验库(启动种子 + w7 沉淀,新踩坑走 w7 不写本文件)
    assert "经验库" in errors_md and "seed" in errors_md and "w7" in errors_md
    assert "新踩坑不写这里" in errors_md
    # 无静态错误码 → 修法映射:errors.md + SKILL.md 全文件清零
    # (旧分类表 CS0246/CS0506/CS0103/CS1061/CS1002 等全部移除,只留经验库种子)
    assert not re.search(r"CS\d{4}", errors_md)
    assert not re.search(r"CS\d{4}", payload["content"])   # SKILL.md 同样不含
    assert "经验条目(seed)" not in errors_md


def test_worker_type_branches_read_from_skill_references():
    """类型分支方法论单源化:worker TYPE_PROMPTS 指向 skills/<skill>/references/,
    prompts/ 下不再有类型分支文件(方法论移走,不重复维护)。"""
    from agents.kingdee_plugin_agent.graph.workers.w2_design import TYPE_PROMPTS as W2
    from agents.kingdee_plugin_agent.graph.workers.w3_generate import TYPE_PROMPTS as W3
    from agents.kingdee_plugin_agent.graph.workers.w4_review import TYPE_PROMPTS as W4

    assert W2["bill"] == "design-builder/references/bill.md"
    assert W3["bill"] == "code-generator/references/bill.md"
    assert W4["bill"] == "code-reviewer/references/bill.md"
    for mapping in (W2, W3, W4):
        assert set(mapping) == {"bill", "service", "list"}
        for v in mapping.values():
            assert "/references/" in v and v.endswith(".md")


@pytest.mark.parametrize("plugin_type", ["bill", "service", "list"])
def test_w2_w3_w4_execute_all_three_types(tmp_path, plugin_type):
    """三类型全覆盖(14→8 worker 配置表等价性):w2/w3/w4 对每个插件类型走
    确定性路径(llm=None)都真实执行 —— 类型知识在骨架路径也完整传递:
    w2 设计含对应类型分支要点文本;w3 代码用对应类型模板(基类按类型写死);
    w4 审查照常产出裁决与审查报告。"""
    from agents.kingdee_plugin_agent.graph.workers.w2_design import DesignWorker
    from agents.kingdee_plugin_agent.graph.workers.w3_generate import GenerateWorker
    from agents.kingdee_plugin_agent.graph.workers.w4_review import ReviewWorker

    store = ArtifactStore(root=tmp_path)
    st = TaskState(requirement_spec={}, todo=[])

    # w2:骨架设计含类型分支要点(bill/service/list 各自的 references 文件内容)
    sub2 = Subtask("A1", plugin_type, "x", [], "pending")
    sub2, msg2 = DesignWorker(llm=None, store=store).run(st, sub2)
    assert sub2.design_path.endswith("design.md")
    design = store.read(sub2.id, "design.md")
    branch = (_Path(__file__).parent.parent / "agents" / "kingdee_plugin_agent" / "skills"
              / "design-builder" / "references" / f"{plugin_type}.md").read_text(encoding="utf-8")
    assert branch.splitlines()[0] in design   # 类型要点已进骨架

    # w3:骨架渲染对应类型模板(基类按类型写死)
    sub3 = Subtask("A1", plugin_type, "x", [], "design_done")
    sub3, msg3 = GenerateWorker(llm=None, store=store).run(st, sub3)
    assert sub3.code_path.endswith("Plugin.cs")
    code = store.read(sub3.id, "Plugin.cs")
    base = {"bill": "AbstractBillPlugIn", "service": "AbstractOperationServicePlugIn",
            "list": "AbstractListPlugIn"}[plugin_type]
    assert base in code
    assert "{{" not in code                     # 全部占位符已渲染

    # w4:干净代码 → Approved + review.json 落盘
    sub4 = Subtask("A1", plugin_type, "x", [], "gen_done")
    sub4, msg4 = ReviewWorker(llm=None, store=store).run(st, sub4)
    assert sub4.review_verdict == "Approved"
    assert sub4.review_path.endswith("review.json")


from langchain_core.messages import AIMessage
from agents.kingdee_plugin_agent.graph.workers.w1_requirement import QuestionsOutput
from agents.kingdee_plugin_agent.skills.loader import load_skill, structured_with_skill


class _ToolAwareLLM:
    """模拟真实模型:bind_tools 能力探测 + with_structured_output(tools, include_raw)。

    invoke 由子类实现;seen 记录每轮 invoke 的输入,so_kwargs 记录
    with_structured_output 收到的参数(验证 tools 真实下发)。
    """

    def __init__(self):
        self.seen = []
        self.so_kwargs = {}

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        self.so_kwargs = kwargs
        return self


def _tool_call_round():
    """回合返回:模型请求调 load_skill(parsed=None)。"""
    return {"raw": AIMessage(content="", tool_calls=[
        {"id": "call_1", "name": "load_skill", "type": "function",
         "args": {"skill_name": "requirement-clarify"}}]),
        "parsed": None}


class _RoundTripLLM(_ToolAwareLLM):
    """回合 1 调工具 → 回合 2 出 schema(验证工具结果喂回)。"""

    def invoke(self, messages):
        self.seen.append(messages)
        if len(self.seen) == 1:
            return _tool_call_round()
        return {"raw": AIMessage(content='{"questions": ["目标单据?"]}'),
                "parsed": QuestionsOutput(questions=["目标单据?"])}


class _NoToolLLM(_ToolAwareLLM):
    """回合 1 直接出 schema(不调工具)。"""

    def invoke(self, messages):
        self.seen.append(messages)
        return {"raw": AIMessage(content='{"questions": ["目标单据?"]}'),
                "parsed": QuestionsOutput(questions=["目标单据?"])}


class _AlwaysToolLLM(_ToolAwareLLM):
    """每回合都调工具(验证 2 回合上限强制停止)。"""

    def invoke(self, messages):
        self.seen.append(messages)
        return _tool_call_round()


class _BadParseLLM(_ToolAwareLLM):
    """模型产出无法解析(parsed=None 且无 tool_calls)。"""

    def invoke(self, messages):
        self.seen.append(messages)
        return {"raw": AIMessage(content=""), "parsed": None,
                "parsing_error": "json decode failed"}


def test_structured_with_skill_binds_tool_and_feeds_result_back():
    """真实模型路径(模拟):load_skill 绑定 → 回合 1 模型调工具 → 执行喂回
    ToolMessage → 回合 2 出 schema。"""
    from langchain_core.messages import ToolMessage

    llm = _RoundTripLLM()
    out = structured_with_skill(llm, QuestionsOutput,
                                [("system", "s"), ("human", "h")])
    assert out.questions == ["目标单据?"]
    # 绑定发生在 with_structured_output 内部(官方 tools 参数),bind_tools
    # 只作能力探测 —— 断言 tools 参数真实下发
    assert llm.so_kwargs["tools"] == [load_skill]
    assert llm.so_kwargs["include_raw"] is True
    assert len(llm.seen) == 2                         # 恰好 2 回合(1 工具 + 1 schema)
    second = llm.seen[1]
    assert any(isinstance(m, ToolMessage) for m in second)
    assert any("金蝶插件需求澄清方法论" in getattr(m, "content", "")
               for m in second)                       # 工具结果真实喂回


def test_structured_with_skill_single_round_when_no_tool_call():
    """回合 1 未调工具 → 单次 invoke,parsed 直接返回(零额外往返)。"""
    llm = _NoToolLLM()
    out = structured_with_skill(llm, QuestionsOutput,
                                [("system", "s"), ("human", "h")])
    assert out.questions == ["目标单据?"]
    assert len(llm.seen) == 1


def test_structured_with_skill_caps_tool_rounds():
    """回合 2 仍调工具 → 返回 None(2 回合上限,防工具调用死循环)。"""
    llm = _AlwaysToolLLM()
    out = structured_with_skill(llm, QuestionsOutput,
                                [("system", "s"), ("human", "h")])
    assert out is None
    assert len(llm.seen) == 2                         # 回合 1 调工具 + 回合 2 仍调 → 停止


def test_structured_with_skill_parse_failure_retries_then_returns_none():
    """畸形 JSON 重试(设计 §8):解析失败重试 1 次(共 2 次尝试),仍失败 → None。

    重试用同一份输入(不喂回失败响应);worker 走确定性骨架降级。
    """
    llm = _BadParseLLM()
    out = structured_with_skill(llm, QuestionsOutput,
                                [("system", "s"), ("human", "h")])
    assert out is None
    assert len(llm.seen) == 2                         # 2 次尝试,输入一致
    assert llm.seen[0] == llm.seen[1]                 # 同输入重试(未掺失败响应)


class _RetryThenSuccessLLM(_ToolAwareLLM):
    """第 1 次解析失败(畸形 JSON)→ 第 2 次成功(重试救回,不再降级骨架)。"""

    def invoke(self, messages):
        self.seen.append(messages)
        if len(self.seen) == 1:
            return {"raw": AIMessage(content=""), "parsed": None,
                    "parsing_error": "json decode failed"}
        return {"raw": AIMessage(content='{"questions": ["重试救回?"]}'),
                "parsed": QuestionsOutput(questions=["重试救回?"])}


def test_structured_with_skill_retry_recovers_after_parse_failure():
    """解析失败 1 次 → 重试成功:结果返回(重试救回,不进确定性骨架)。"""
    llm = _RetryThenSuccessLLM()
    out = structured_with_skill(llm, QuestionsOutput,
                                [("system", "s"), ("human", "h")])
    assert out.questions == ["重试救回?"]
    assert len(llm.seen) == 2                         # 失败 1 次 + 重试成功 1 次
