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
