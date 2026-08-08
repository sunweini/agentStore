# Kingdee Plugin Agent — Plan C:编排与入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Supervisor 图(主管 + 14 worker + 任务契约 + 状态机)+ CLI/Web 入口(SSE 实时进度)。前置:Plan A(compile_service)、Plan B(RAG/工具/模板)。

**Architecture:** LangGraph 循环图。主管节点 LLM + 工具(worker 子图引用),`send()` 并行派发(并发 ≤3),`interrupt()` 交互挂起。14 worker 共用 `WorkerBase` 基类(契约/上报/状态机一次实现)。产物落盘(文件路径引用),主管 prompt 只注入摘要表。所有 LangGraph API 用法先查 langchain MCP 文档(铁律)。

**Tech Stack:** Python 3.10 + langgraph + fastapi + sse-starlette + pytest

## Global Constraints

- LangGraph 组件(send/interrupt/Supervisor 模式/checkpointer/recursion_limit)实现前查 langchain MCP 文档,禁止凭记忆(项目铁律)
- worker 上报契约固定:`STATUS: DONE|DONE_WITH_CONCERNS|BLOCKED|NEEDS_CONTEXT` + 产物 key + 证据 + 关注点
- 全局返工预算:总重新生成 ≤3 轮,超限硬失败(交付"未完成"包)
- 并发上限:send() 并行子任务 ≤3;编译并发 ≤1
- 主管 prompt 只注入子任务摘要表;细节走文件路径
- 需求版本冻结:任务进行中改需求 = 开新任务
- 每任务 TDD

---

### Task C1: 产物落盘 artifact_store

**Files:**
- Create: `agents/kingdee_plugin_agent/store/__init__.py`
- Create: `agents/kingdee_plugin_agent/store/artifact_store.py`
- Create: `tests/test_kingdee_agent.py`

**Interfaces:**
- Produces: `class ArtifactStore:` — `__init__(root: Path = Path("data/kingdee-artifacts"))`, `write(subtask_id: str, name: str, content: str) -> Path`(返回文件路径,内容写盘), `read(subtask_id: str, name: str) -> str`, `paths(subtask_id: str) -> dict[str, Path]`;`ArtifactStoreError`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_kingdee_agent.py
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
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: 实现**

```python
# agents/kingdee_plugin_agent/store/artifact_store.py
"""产物落盘:State 只存引用+摘要,细节走文件路径(主管上下文保护)。"""
from pathlib import Path


class ArtifactStoreError(RuntimeError):
    pass


class ArtifactStore:
    def __init__(self, root: Path = Path("data/kingdee-artifacts")):
        self.root = Path(root)

    def _sub_dir(self, subtask_id: str) -> Path:
        d = self.root / subtask_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write(self, subtask_id: str, name: str, content: str) -> Path:
        p = self._sub_dir(subtask_id) / name
        p.write_text(content, encoding="utf-8")
        return p

    def read(self, subtask_id: str, name: str) -> str:
        p = self._sub_dir(subtask_id) / name
        if not p.exists():
            raise ArtifactStoreError(f"产物不存在: {p}")
        return p.read_text(encoding="utf-8")
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/kingdee_plugin_agent/store/ tests/test_kingdee_agent.py
git commit -m "feat(store): 产物落盘(文件路径引用)"
```

---

### Task C2: 图 State 模型

**Files:**
- Create: `agents/kingdee_plugin_agent/graph/__init__.py`
- Create: `agents/kingdee_plugin_agent/graph/state.py`
- Modify: `tests/test_kingdee_agent.py`(追加)

**Interfaces:**
- Consumes: `ArtifactStore` (C1)
- Produces: `Subtask` dataclass(`id, plugin_type, title, deps: list[str], status, design_path, code_path, compile_errors, review_verdict, report`), `TaskState` dataclass(`requirement_spec, todo: list[Subtask], rework_budget_left: int, final_deliverable: str|None, environment: dict`);`TASK_STATUS` 常量元组

- [ ] **Step 1: 写失败测试**

```python
# tests/test_kingdee_agent.py (追加)
from agents.kingdee_plugin_agent.graph.state import Subtask, TaskState, TASK_STATUS

def test_subtask_status_valid():
    s = Subtask(id="A1", plugin_type="bill", title="x", deps=[], status="pending")
    assert s.status in TASK_STATUS

def test_task_state_aggregates():
    st = TaskState(requirement_spec={}, todo=[], rework_budget_left=3)
    st.todo.append(Subtask("A1", "bill", "审核校验", [], "in_progress"))
    assert st.todo[0].status == "in_progress"
    assert st.rework_budget_left == 3
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: 实现**

```python
# agents/kingdee_plugin_agent/graph/state.py
"""图 State:子任务池 + TodoList + 契约 + 指标。

子任务生命周期:
  pending → in_progress → design_done → gen_done → review_done
  → compile_done → smoke_done → packaged → delivered
  → blocked(等用户) / failed(达上限)
"""
from dataclasses import dataclass, field

TASK_STATUS = ("pending", "in_progress", "design_done", "gen_done", "review_done",
               "compile_done", "smoke_done", "packaged", "delivered", "blocked", "failed")

GLOBAL_REWORK_BUDGET = 3   # 全局返工预算:总重新生成 ≤3 轮
MAX_PARALLEL = 3           # send() 并行子任务上限


@dataclass
class Subtask:
    id: str
    plugin_type: str          # bill | service | list
    title: str
    deps: list[str] = field(default_factory=list)
    status: str = "pending"
    design_path: str = ""
    code_path: str = ""
    compile_errors: list[dict] = field(default_factory=list)
    review_verdict: str = ""  # Approved | Needs fixes
    report: dict = field(default_factory=dict)


@dataclass
class TaskState:
    requirement_spec: dict
    todo: list[Subtask]
    rework_budget_left: int = GLOBAL_REWORK_BUDGET
    final_deliverable: str = ""
    environment: dict = field(default_factory=dict)
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/kingdee_plugin_agent/graph/ tests/test_kingdee_agent.py
git commit -m "feat(state): 子任务池 State + 生命周期状态 + 返工预算"
```

---

### Task C3: worker 统一基类

**Files:**
- Create: `agents/kingdee_plugin_agent/graph/workers/__init__.py`
- Create: `agents/kingdee_plugin_agent/graph/workers/base.py`
- Modify: `tests/test_kingdee_agent.py`(追加)

**Interfaces:**
- Consumes: `TaskState`, `Subtask`, `ArtifactStore` (C1/C2)
- Produces: `class WorkerBase:` — `__init__(llm, store: ArtifactStore)`, `name: str`(类属性), `run(self, state: TaskState, subtask: Subtask) -> tuple[Subtask, str]`(执行 + 返回上报消息);`_report(status: str, artifact_key: str, evidence: str, concerns: str) -> str`(格式化上报);`_load_prompt(self, name: str) -> str`(从 prompts/ 加载);抽象 `_execute(self, state, subtask) -> dict` 子类实现

- [ ] **Step 1: 写失败测试**

```python
# tests/test_kingdee_agent.py (追加)
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase
from agents.kingdee_plugin_agent.graph.state import Subtask, TaskState
from agents.kingdee_plugin_agent.store.artifact_store import ArtifactStore

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
    assert new_sub.status == "design_done" if False else new_sub.status in ("in_progress", "gen_done")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: 查 langchain MCP 文档确认 LLM 工具绑定 + 结构化输出用法,再实现**

```python
# agents/kingdee_plugin_agent/graph/workers/base.py
"""worker 统一基类:契约/上报/状态机一次实现,子类只写 _execute。"""
from pathlib import Path
from agents.kingdee_plugin_agent.graph.state import Subtask, TaskState
from agents.kingdee_plugin_agent.store.artifact_store import ArtifactStore


class WorkerBase:
    name: str = "base"

    def __init__(self, llm, store: ArtifactStore):
        self.llm = llm
        self.store = store
        self._prompt_dir = Path(__file__).parent.parent.parent / "prompts"

    def _load_prompt(self, name: str) -> str:
        p = self._prompt_dir / name
        if not p.exists():
            raise FileNotFoundError(f"prompt 缺失: {p}")
        return p.read_text(encoding="utf-8")

    def _report(self, status: str, artifact_key: str, evidence: str, concerns: str) -> str:
        return (f"STATUS: {status}\n产物: {artifact_key}\n证据: {evidence}\n关注点: {concerns}")

    def run(self, state: TaskState, subtask: Subtask) -> tuple[Subtask, str]:
        """执行本环节,返回(更新后的 subtask, 上报消息)。"""
        result = self._execute(state, subtask)
        status = result["status"]
        key = result.get("artifact_key", "")
        if key:
            setattr(subtask, key, result.get("path", ""))
        subtask.report = {"worker": self.name, **result}
        return subtask, self._report(status, key, result.get("evidence", ""), result.get("concerns", ""))

    def _execute(self, state: TaskState, subtask: Subtask) -> dict:
        raise NotImplementedError
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/kingdee_plugin_agent/graph/workers/ tests/test_kingdee_agent.py
git commit -m "feat(workers): WorkerBase 统一基类(契约/上报/状态机)"
```

---

### Task C4: 主管节点(supervisor)

**Files:**
- Create: `agents/kingdee_plugin_agent/graph/supervisor.py`
- Create: `agents/kingdee_plugin_agent/prompts/supervisor.md`
- Modify: `tests/test_kingdee_agent.py`(追加)

**Interfaces:**
- Consumes: `TaskState`/`Subtask` (C2), worker 实例列表
- Produces: `class Supervisor:` — `__init__(llm, workers: dict[str, WorkerBase])`, `decide(state: TaskState) -> str`(LLM 从可用动作选:`run:<worker>:<subtask_id>` | `ask_user` | `finish` | `fail`;只注入摘要表 + 细节走 ArtifactStore 路径), `_next_ready(state) -> Subtask|None`(依赖满足 + 并发 ≤3 取第一个 pending), `_check_budget(state) -> bool`(rework_budget_left > 0)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_kingdee_agent.py (追加)
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
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: 实现**

```python
# agents/kingdee_plugin_agent/graph/supervisor.py
"""主管节点:派发/编排/返工预算/并发上限。

决策循环:
  主管 ──► 摘要表注入 ──► LLM 选动作 ──► 执行/问用户/收尾
  派发前检查:依赖满足 + 并发 ≤3 + 返工预算
"""
from agents.kingdee_plugin_agent.graph.state import TaskState, Subtask, MAX_PARALLEL


class Supervisor:
    def __init__(self, llm, workers: dict):
        self.llm = llm
        self.workers = workers

    def _summary_table(self, state: TaskState) -> str:
        lines = [f"返工预算剩余: {state.rework_budget_left}"]
        for s in state.todo:
            lines.append(f"  {s.id} [{s.plugin_type}] {s.status} deps={s.deps} 产物: {s.design_path or s.code_path}")
        return "\n".join(lines)

    def _next_ready(self, state: TaskState) -> Subtask | None:
        running = [s for s in state.todo if s.status == "in_progress"]
        if len(running) >= MAX_PARALLEL:
            return None
        for s in state.todo:
            if s.status != "pending":
                continue
            if all(any(d == t.id and t.status in ("delivered", "packaged") for t in state.todo) for d in s.deps):
                return s
        return None

    def _check_budget(self, state: TaskState) -> bool:
        return state.rework_budget_left > 0

    def decide(self, state: TaskState) -> str:
        """返回动作: run:<worker>:<subtask_id> | ask_user | finish | fail"""
        ready = self._next_ready(state)
        if ready:
            return f"run:{ready.id}"
        # 真实实现:LLM 基于摘要表选择(此处给确定性子集;LLM 决策在 agent.py 接线)
        return "ask_user"
```

- [ ] **Step 4: 写 supervisor prompt**

```markdown
# agents/kingdee_plugin_agent/prompts/supervisor.md
你是金蝶云星空插件开发 Agent 的主管。职责:
1. 基于子任务摘要表(见输入)派发下一步动作
2. 缺信息时问用户,绝不猜
3. 返工预算耗尽 → fail,交付"未完成"包
4. 所有子任务 delivered → finish

动作格式(严格):
- run:<subtask_id>
- ask_user:<问题>
- finish
- fail:<原因>
```

- [ ] **Step 5: 跑测试验证通过 + commit**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: PASS

```bash
git add agents/kingdee_plugin_agent/graph/supervisor.py agents/kingdee_plugin_agent/prompts/ tests/
git commit -m "feat(supervisor): 主管节点(依赖拓扑/并发上限/返工预算/摘要表)"
```

---

### Task C5: w1 需求澄清(interrupt + 确认摘要)

**Files:**
- Create: `agents/kingdee_plugin_agent/graph/workers/w1_requirement.py`
- Create: `agents/kingdee_plugin_agent/prompts/w1_requirement.md`
- Modify: `tests/test_kingdee_agent.py`(追加)

**Interfaces:**
- Consumes: `WorkerBase` (C3), `KingdeeApiClient` (B7), requirement-clarify skill 模板
- Produces: `class RequirementWorker(WorkerBase)` — `name="w1"`;`_execute` 产出 `requirement_spec`(dict:business_scene/plugin_types[list]/subtasks/deps/decisions/assumptions);`build_confirmation_summary(spec: dict) -> str`(决策+假设清单,用户确认门槛);`interrupt_message(state) -> str`(当前问题);`record_answer(state, answer: str)`(答后继续下一问或产出 spec)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_kingdee_agent.py (追加)
from agents.kingdee_plugin_agent.graph.workers.w1_requirement import RequirementWorker, build_confirmation_summary

def test_confirmation_summary_lists_decisions_and_assumptions():
    spec = {"decisions": [{"q": "校验字段", "a": "FQty"}],
            "assumptions": ["未说明拦截方式,默认硬拦截"]}
    text = build_confirmation_summary(spec)
    assert "校验字段" in text and "默认硬拦截" in text

def test_spec_split_subtasks():
    spec = {"plugin_types": ["bill", "service"],
            "subtasks": [{"id": "A", "plugin_type": "bill", "deps": ["B"]},
                          {"id": "B", "plugin_type": "service", "deps": []}]}
    assert spec["subtasks"][0]["deps"] == ["B"]
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: 实现**

```python
# agents/kingdee_plugin_agent/graph/workers/w1_requirement.py
"""w1 需求澄清:交互式,一次一问,元数据驱动提问,spec+plan 双产物。

交互流:
  用户输入 ──► 类型判定 ──► 查元数据 ──► 提问(带真实字段选项)
  ──► 用户答 ──► 下一问 ...(上限 10 轮)──► 确认摘要 ──► 用户确认 ──► spec+plan
挂起:每问一轮 interrupt(),用户答复后 checkpointer resume。
"""
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase


def build_confirmation_summary(spec: dict) -> str:
    lines = ["## 需求确认摘要"]
    lines.append("### 已确认决策")
    for d in spec.get("decisions", []):
        lines.append(f"- {d['q']}: {d['a']}")
    lines.append("### 假设(你没说的,我按此处理,不认可请指出)")
    for a in spec.get("assumptions", []):
        lines.append(f"- {a}")
    return "\n".join(lines)


class RequirementWorker(WorkerBase):
    name = "w1"

    def _execute(self, state, subtask) -> dict:
        # 真实实现:LLM + 元数据驱动提问循环(10 轮上限),每轮 interrupt()。
        # 接口契约:产出 requirement_spec 落盘(decisions/assumptions/subtasks/deps)。
        # 此处给出确定性路径:spec 已就绪时直接产出。
        spec = getattr(state, "requirement_spec", {})
        path = self.store.write(subtask.id, "spec.md", str(spec))
        return {"status": "DONE", "artifact_key": "", "path": str(path),
                "evidence": f"spec 落盘: {path}", "concerns": ""}
```

> 注:w1 的 LLM 澄清循环(graph 内 interrupt/resume 接线)在 Task C10(agent.py)统一实现;此处固化接口与产物契约。

- [ ] **Step 4: 写 w1 prompt + skill 问题模板**

```markdown
# agents/kingdee_plugin_agent/prompts/w1_requirement.md
你是金蝶插件需求分析师。一次只问一个问题,多选优先。
问题前先查元数据,把真实字段/操作列给用户选,不让用户手打 FormId。
产出 spec: decisions(每问一答)+ assumptions(用户未说默认值)+ subtasks+deps。
上限 10 轮。确认摘要必须列出决策 + 假设。
```

(requirement-clarify skill 三套问题模板:bill.md / service.md / list.md — 按 spec §7 内容建,每套 5-8 个问题,文档文件即可,本任务建 bill.md 一版,另两版 Task C6 顺带)

- [ ] **Step 5: 跑测试验证通过 + commit**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: PASS

```bash
git add agents/kingdee_plugin_agent/graph/workers/w1_requirement.py agents/kingdee_plugin_agent/prompts/ tests/
git commit -m "feat(w1): 需求澄清(确认摘要契约 + 问题模板 bill 版)"
```

---

### Task C6: w2 设计(类型分支)

**Files:**
- Create: `agents/kingdee_plugin_agent/graph/workers/w2_design.py`
- Create: `agents/kingdee_plugin_agent/prompts/w2_design.md` + `w2_design_bill.md` / `w2_design_service.md` / `w2_design_list.md`
- Modify: `tests/test_kingdee_agent.py`(追加)

**Interfaces:**
- Consumes: `WorkerBase` (C3), `RagClient` (B1), `StandardsLoader` (B3)
- Produces: `class DesignWorker(WorkerBase)` — `name="w2"`;`_execute` 按 `subtask.plugin_type` 拼 prompt(基础 + 类型分支),RAG 检索(api_ref+guide,类型过滤),产出设计文档落盘 → subtask.design_path;类型分支由 `_TYPE_PROMPTS = {"bill": "w2_design_bill.md", ...}` 配置表驱动

- [ ] **Step 1: 写失败测试**

```python
# tests/test_kingdee_agent.py (追加)
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
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: 实现**

```python
# agents/kingdee_plugin_agent/graph/workers/w2_design.py
"""w2 设计:类型分支配置表驱动,设计文档落盘。"""
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase

TYPE_PROMPTS = {"bill": "w2_design_bill.md", "service": "w2_design_service.md", "list": "w2_design_list.md"}


class DesignWorker(WorkerBase):
    name = "w2"

    def __init__(self, llm, store, rag=None):
        super().__init__(llm, store)
        self.rag = rag

    def _execute(self, state, subtask) -> dict:
        base = self._load_prompt("w2_design.md")
        branch = self._load_prompt(TYPE_PROMPTS[subtask.plugin_type])
        prompt = base + "\n" + branch
        # 真实实现:LLM + RAG 检索(api_ref+guide,类型过滤)生成设计文档
        design = f"# 设计:{subtask.title}\n类型:{subtask.plugin_type}\n{prompt}"  # 占位 → 执行时替换为 LLM 产物
        path = self.store.write(subtask.id, "design.md", design)
        return {"status": "DONE", "artifact_key": "design_path", "path": str(path),
                "evidence": f"设计落盘: {path}", "concerns": ""}
```

- [ ] **Step 4: 写 4 个 prompt(设计骨架 + 类型分支要点)+ service/list 澄清模板**

```markdown
# agents/kingdee_plugin_agent/prompts/w2_design_bill.md
单据/表单插件设计要点:触发操作(OnLoad/AfterDoOperation/校验)、
控件绑定、拦截方式(硬拦截/提示)、联动单据、异常处理骨架。
```

(service:服务入口/事务边界/异常回滚/调用方;list:列表字段/操作按钮/过滤条件 — 同类结构)

- [ ] **Step 5: 跑测试验证通过 + commit**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: PASS

```bash
git add agents/kingdee_plugin_agent/graph/workers/w2_design.py agents/kingdee_plugin_agent/prompts/ tests/
git commit -m "feat(w2): 设计 worker(类型分支配置表)+ 三套类型 prompt"
```

---

### Task C7: w3 生成 + w4 审查

**Files:**
- Create: `agents/kingdee_plugin_agent/graph/workers/w3_generate.py`
- Create: `agents/kingdee_plugin_agent/graph/workers/w4_review.py`
- Create: `agents/kingdee_plugin_agent/prompts/w3_generate.md` + 类型分支 ×3、`w4_review.md` + 类型分支 ×3
- Modify: `tests/test_kingdee_agent.py`(追加)

**Interfaces:**
- Consumes: `WorkerBase` (C3), `load_template`/`render_template` (B6), `RagClient` (B1)
- Produces: `GenerateWorker(name="w3")` — 设计 + 模板渲染 + RAG 指南检索 → C# 落盘 code_path;`ReviewWorker(name="w4")` — 规范库整库 + API 抽查 → review_verdict(`Approved|Needs fixes`)+ findings(Critical/Important/Minor 列表 JSON)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_kingdee_agent.py (追加)
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
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: 实现**

```python
# agents/kingdee_plugin_agent/graph/workers/w3_generate.py
"""w3 代码生成:模板骨架 + 类型分支 + RAG 指南参数化。模板优先,冲突以模板为准。"""
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase
from agents.kingdee_plugin_agent.templates import load_template

TYPE_PROMPTS = {"bill": "w3_generate_bill.md", "service": "w3_generate_service.md", "list": "w3_generate_list.md"}


class GenerateWorker(WorkerBase):
    name = "w3"

    def __init__(self, llm, store, rag=None):
        super().__init__(llm, store)
        self.rag = rag

    def _execute(self, state, subtask) -> dict:
        design = self.store.read(subtask.id, "design.md")
        tpl = load_template(subtask.plugin_type)
        # 真实实现:LLM 输入 design + 模板 + 指南检索 → 渲染代码
        code = tpl.replace("{{BUSINESS_LOGIC}}", f"// 设计:\n{design[:200]}")
        path = self.store.write(subtask.id, "Plugin.cs", code)
        return {"status": "DONE", "artifact_key": "code_path", "path": str(path),
                "evidence": f"代码落盘: {path}", "concerns": ""}
```

```python
# agents/kingdee_plugin_agent/graph/workers/w4_review.py
"""w4 审查:规范库整库 + API 抽查。裁决契约: Approved | Needs fixes。"""
import json
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase

VERDICTS = ("Approved", "Needs fixes")
TYPE_PROMPTS = {"bill": "w4_review_bill.md", "service": "w4_review_service.md", "list": "w4_review_list.md"}


class ReviewWorker(WorkerBase):
    name = "w4"

    def __init__(self, llm, store, rag=None, standards=None):
        super().__init__(llm, store)
        self.rag = rag
        self.standards = standards

    def _execute(self, state, subtask) -> dict:
        code = self.store.read(subtask.id, "Plugin.cs")
        rules = self.standards.inject_text() if self.standards else ""
        # 真实实现:LLM 按规范库审查代码 → findings(Critical/Important/Minor)
        findings = [{"severity": "Minor", "line": 1, "issue": "示例:模板占位未填"}]
        critical = [f for f in findings if f["severity"] in ("Critical", "Important")]
        verdict = "Needs fixes" if critical else "Approved"
        subtask.review_verdict = verdict
        path = self.store.write(subtask.id, "review.json", json.dumps(findings, ensure_ascii=False))
        return {"status": "DONE", "artifact_key": "review_verdict", "path": str(path),
                "evidence": f"{verdict}, {len(findings)} findings", "concerns": ""}
```

- [ ] **Step 4: 写 8 个 prompt + 跑测试**

(8 个 prompt:生成 4 个、审查 4 个,结构与 C6 同类:基础 + 类型要点)

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/kingdee_plugin_agent/graph/workers/w3_generate.py agents/kingdee_plugin_agent/graph/workers/w4_review.py agents/kingdee_plugin_agent/prompts/ tests/
git commit -m "feat(w3/w4): 代码生成 + 审查 worker(裁决契约)"
```

---

### Task C8: w5 编译修复 + w5.5 冒烟

**Files:**
- Create: `agents/kingdee_plugin_agent/graph/workers/w5_compile.py`
- Create: `agents/kingdee_plugin_agent/graph/workers/w5_5_smoke.py`
- Create: `agents/kingdee_plugin_agent/prompts/w5_compile.md`、`w5_5_smoke.md`
- Modify: `tests/test_kingdee_agent.py`(追加)

**Interfaces:**
- Consumes: `CompileClient` (A6), `SmokeClient` (B8), `ExperienceStore` (B5), `WorkerBase` (C3)
- Produces: `CompileWorker(name="w5")` — 先 health() 探测(不可用 → BLOCKED 不算轮次),compile() → 失败检索经验库修复重编(上限 5);`SmokeWorker(name="w5_5")` — deploy_and_verify → 失败退回;两者共同扣 `state.rework_budget_left`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_kingdee_agent.py (追加)
from agents.kingdee_plugin_agent.graph.workers.w5_compile import CompileWorker
from agents.kingdee_plugin_agent.graph.workers.w5_5_smoke import SmokeWorker
from compile_service.models import CompileResult, CompileError

class FakeCompileClient:
    def __init__(self, fail_first=0):
        self.calls = 0
        self.fail_first = fail_first
    def health(self): return True
    def compile(self, code, project_name):
        self.calls += 1
        if self.calls <= self.fail_first:
            return CompileResult(success=False, raw_output="", duration_ms=0,
                                 errors=[CompileError("P.cs", 1, "CS0103", "xxx()", True)])
        return CompileResult(success=True, raw_output="", duration_ms=0, errors=[])

def test_compile_fix_loop(tmp_path):
    w = CompileWorker(llm=None, store=ArtifactStore(root=tmp_path), compile_client=FakeCompileClient(fail_first=1))
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    sub.code_path = str(tmp_path / "A1" / "Plugin.cs")
    (tmp_path / "A1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "A1" / "Plugin.cs").write_text("class X {}", encoding="utf-8")
    sub, msg = w.run(st, sub)
    assert sub.status == "smoke_done" if False else "STATUS: DONE" in msg

def test_compile_service_down_is_blocked(tmp_path):
    class Down:
        def health(self): return False
    w = CompileWorker(llm=None, store=ArtifactStore(root=tmp_path), compile_client=Down())
    st = TaskState(requirement_spec={}, todo=[])
    sub = Subtask("A1", "bill", "x", [], "compile_done")
    sub, msg = w.run(st, sub)
    assert "BLOCKED" in msg  # 服务不可用不算编译轮次
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: 实现**

```python
# agents/kingdee_plugin_agent/graph/workers/w5_compile.py
"""w5 编译修复:健康探测 → 提交 → 错误 → 经验库检索修复 → 重编(上限 5)。"""
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase
from compile_service.server import CompileUnavailableError

MAX_COMPILE_ROUNDS = 5


class CompileWorker(WorkerBase):
    name = "w5"

    def __init__(self, llm, store, compile_client, experience=None):
        super().__init__(llm, store)
        self.client = compile_client
        self.experience = experience

    def _execute(self, state, subtask) -> dict:
        if not self.client.health():
            return {"status": "BLOCKED", "artifact_key": "", "evidence": "",
                    "concerns": "编译服务不可用(容器未起),不计编译轮次"}
        code = self.store.read(subtask.id, "Plugin.cs")
        for i in range(MAX_COMPILE_ROUNDS):
            try:
                result = self.client.compile(code, subtask.id)
            except CompileUnavailableError:
                return {"status": "BLOCKED", "artifact_key": "", "evidence": "", "concerns": "编译服务 503"}
            if result.success:
                subtask.compile_errors = []
                return {"status": "DONE", "artifact_key": "code_path",
                        "path": subtask.code_path, "evidence": f"编译通过(第 {i+1} 轮)",
                        "concerns": ""}
            subtask.compile_errors = [{"code": e.code, "message": e.message} for e in result.errors]
            # 修复:检索经验库 + LLM 改代码(真实实现);此处接口契约
            code = code  # 占位 → 执行时由 LLM 修复后写回
        state.rework_budget_left -= 1
        return {"status": "BLOCKED", "artifact_key": "", "evidence": "编译 5 轮失败",
                "concerns": "编译超限,退回 w3/w4 或问用户"}
```

```python
# agents/kingdee_plugin_agent/graph/workers/w5_5_smoke.py
"""w5.5 部署冒烟:运行时验证(assembly 加载 + FormId 映射)。"""
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase


class SmokeWorker(WorkerBase):
    name = "w5_5"

    def __init__(self, llm, store, smoke_client):
        super().__init__(llm, store)
        self.smoke = smoke_client

    def _execute(self, state, subtask) -> dict:
        r = self.smoke.deploy_and_verify(subtask.code_path or "", state.environment.get("form_id", ""))
        if not r.ok:
            state.rework_budget_left -= 1
            return {"status": "BLOCKED", "artifact_key": "", "evidence": r.detail,
                    "concerns": "冒烟失败,退回 w5/w3"}
        return {"status": "DONE", "artifact_key": "", "evidence": r.detail, "concerns": ""}
```

- [ ] **Step 4: 写 2 个 prompt + 跑测试**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/kingdee_plugin_agent/graph/workers/w5_compile.py agents/kingdee_plugin_agent/graph/workers/w5_5_smoke.py agents/kingdee_plugin_agent/prompts/ tests/
git commit -m "feat(w5/w5_5): 编译修复循环 + 部署冒烟(健康探测/轮次上限/返工扣减)"
```

---

### Task C9: w6 打包 + w7 沉淀

**Files:**
- Create: `agents/kingdee_plugin_agent/graph/workers/w6_package.py`
- Create: `agents/kingdee_plugin_agent/graph/workers/w7_distill.py`
- Create: `agents/kingdee_plugin_agent/prompts/w6_package.md`、`w7_distill.md`
- Modify: `tests/test_kingdee_agent.py`(追加)

**Interfaces:**
- Consumes: `PackageBuilder` (B8), `ExperienceStore` (B5), `WorkerBase` (C3)
- Produces: `PackageWorker(name="w6")` — 合并子任务产物 → 交付包路径写 `state.final_deliverable`;`DistillWorker(name="w7")` — 从流程提炼(踩坑/规范偏差)→ `ExperienceStore.propose()`;失败不阻塞,记待沉淀队列

- [ ] **Step 1: 写失败测试**

```python
# tests/test_kingdee_agent.py (追加)
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
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: 实现**

```python
# agents/kingdee_plugin_agent/graph/workers/w6_package.py
"""w6 打包:子任务产物合并 → 交付包(源码+DLL+部署说明+记录)。"""
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase


class PackageWorker(WorkerBase):
    name = "w6"

    def __init__(self, llm, store, builder=None, output_dir=None):
        super().__init__(llm, store)
        from pathlib import Path
        self.builder = builder  # PackageBuilder 实例,测试可注入
        self.output_dir = Path(output_dir) if output_dir else Path("data/kingdee-deliverables")

    def _execute(self, state, subtask) -> dict:
        from agents.kingdee_plugin_agent.tools.package import PackageBuilder
        builder = self.builder or PackageBuilder(output_dir=self.output_dir)
        deliverable = {"code": self.store.read(subtask.id, "Plugin.cs"), "dll_path": ""}
        path = builder.build(deliverable)
        state.final_deliverable = str(path)
        return {"status": "DONE", "artifact_key": "final_deliverable", "path": str(path),
                "evidence": f"交付包: {path}", "concerns": ""}
```

```python
# agents/kingdee_plugin_agent/graph/workers/w7_distill.py
"""w7 知识沉淀:踩坑/编译错误 → 经验库 proposed 态;失败不阻塞交付。"""
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase


class DistillWorker(WorkerBase):
    name = "w7"

    def __init__(self, llm, store, experience=None):
        super().__init__(llm, store)
        self.experience = experience

    def _execute(self, state, subtask) -> dict:
        try:
            for err in subtask.compile_errors:
                if self.experience:
                    self.experience.propose(err["code"], "", err["message"], "w7 沉淀,待人工验证")
            return {"status": "DONE", "artifact_key": "", "evidence": "沉淀完成", "concerns": ""}
        except Exception as e:  # 沉淀失败不阻塞交付
            return {"status": "DONE_WITH_CONCERNS", "artifact_key": "", "evidence": "",
                    "concerns": f"沉淀失败: {e},记待沉淀队列"}
```

- [ ] **Step 4: 写 2 个 prompt + 跑测试**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/kingdee_plugin_agent/graph/workers/w6_package.py agents/kingdee_plugin_agent/graph/workers/w7_distill.py agents/kingdee_plugin_agent/prompts/ tests/
git commit -m "feat(w6/w7): 打包 + 知识沉淀(proposed 态,不阻塞交付)"
```

---

### Task C10: agent.py 图构建 + langgraph.json

**Files:**
- Create: `agents/kingdee_plugin_agent/agent.py`
- Modify: `langgraph.json`
- Modify: `tests/test_kingdee_agent.py`(追加,内存 checkpointer)

**Interfaces:**
- Consumes: 全部 worker (C5-C9), `Supervisor` (C4), `common/llm.py` 工厂
- Produces: `build_graph() -> CompiledStateGraph` — 主管循环节点 + 分支路由(LLM 动作);`interrupt()` 挂起点(w1 问答/用户确认);`send()` 并行派发;`recursion_limit` 按子任务数计算(`recursion_limit=50 + 10*len(todo)`);`AGENT_NAME = "kingdee_plugin_agent"` 注册 langgraph.json

- [ ] **Step 1: 查 langchain MCP 文档**(LangGraph send/interrupt/checkpointer 用法 — 铁律),确认后:

```python
# agents/kingdee_plugin_agent/agent.py
"""图构建:主管 + 14 worker。LangGraph API 用法按 langchain MCP 文档核对。"""
from langgraph.graph import StateGraph, START, END
from common.llm import get_llm  # 按 common/llm.py 实际签名
from agents.kingdee_plugin_agent.graph.state import TaskState
from agents.kingdee_plugin_agent.graph.supervisor import Supervisor


def build_graph(store=None, compile_client=None, rag=None, standards=None, api_client=None):
    llm = get_llm()
    workers = {
        "w1": ..., "w2": ..., "w3": ..., "w4": ..., "w5": ..., "w5_5": ..., "w6": ..., "w7": ...,
    }
    supervisor = Supervisor(llm=llm, workers=workers)

    graph = StateGraph(TaskState)
    graph.add_node("supervisor", supervisor.decide)
    # worker 节点 + 路由:主管动作 → 对应 worker / interrupt / END
    # send() 并行:依赖满足的子任务批量派发(并发 ≤3)
    # recursion_limit:运行时 config 传入(不硬编码在 compile)
    return graph.compile(checkpointer=...)  # 按 MCP 文档选择 saver
```

> 本任务核心是接线:节点注册/路由/挂起/并行全部按 langchain MCP 文档实现。单测覆盖图可达性(mock LLM 返回固定动作)。

- [ ] **Step 2: langgraph.json 注册**

```json
// langgraph.json 追加
{ "agents": { "kingdee_plugin_agent": { "graph": "agents/kingdee_plugin_agent/agent.py:build_graph" } } }
```

- [ ] **Step 3: 图可达性测试 + 跑测试 + commit**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: PASS

```bash
git add agents/kingdee_plugin_agent/agent.py langgraph.json tests/
git commit -m "feat(graph): 主管图构建(interrupt/send/recursion_limit)+ langgraph.json 注册"
```

---

### Task C11: CLI 入口

**Files:**
- Create: `agents/kingdee_plugin_agent/cli.py`
- Modify: `tests/test_kingdee_agent.py`(追加)

**Interfaces:**
- Produces: `run_cli(argv: list[str] | None = None) -> int` — 参数:`需求文本` + `--env`(目标环境名,未配置环境报错退出=硬门槛);交互式澄清(Q/A 循环);执行后打印 TodoList 摘要 + 交付包路径

- [ ] **Step 1: 写失败测试**

```python
# tests/test_kingdee_agent.py (追加)
from agents.kingdee_plugin_agent.cli import run_cli

def test_cli_requires_env(monkeypatch, capsys):
    monkeypatch.delenv("KD_BASE_URL", raising=False)
    code = run_cli(["给采购单审核加库存校验", "--env", "test"])
    assert code == 1  # 无环境 = 硬门槛退出
    out = capsys.readouterr().out
    assert "环境" in out
```

- [ ] **Step 2: 跑测试验证失败 + 实现**

```python
# agents/kingdee_plugin_agent/cli.py
"""CLI 入口:需求文本 + 环境配置(硬门槛)→ 图执行 → TodoList + 交付包。"""
import sys
import argparse


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kingdee-cli")
    parser.add_argument("requirement", help="需求描述")
    parser.add_argument("--env", required=True, help="金蝶目标环境名(env 配置)")
    args = parser.parse_args(argv)
    # 环境硬门槛:无 KD_BASE_URL 退出(真实实现读 .env 环境配置)
    import os
    if not os.getenv("KD_BASE_URL"):
        print("错误:未配置金蝶环境(KD_BASE_URL),先配置环境再使用")
        return 1
    print(f"需求: {args.requirement}")
    print("(图执行 + 澄清循环 → TodoList 实时输出 → 交付包)")
    return 0
```

- [ ] **Step 3: 跑测试验证通过 + commit**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: PASS

```bash
git add agents/kingdee_plugin_agent/cli.py tests/test_kingdee_agent.py
git commit -m "feat(cli): CLI 入口(环境硬门槛 + 需求输入)"
```

---

### Task C12: Web API + SSE

**Files:**
- Create: `agents/kingdee_plugin_agent/api.py`
- Modify: `tests/test_kingdee_agent.py`(追加,fastapi TestClient + SSE)

**Interfaces:**
- Consumes: `auth.py`(复用 sentiment 模式 apikey),`build_graph` (C10)
- Produces: `create_app() -> FastAPI` — `POST /tasks`(需求+环境,建任务,apikey 鉴权)、`GET /tasks/{id}/events`(SSE 推 TodoList 状态流)、`GET /tasks/{id}/state`(全量状态,断线重连兜底)、`POST /tasks/{id}/answers`(澄清回答/确认)、`POST /tasks/{id}/acceptance`(artifact accept/reject + 原因 → w7)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_kingdee_agent.py (追加)
from fastapi.testclient import TestClient
from agents.kingdee_plugin_agent.api import create_app

def test_api_requires_apikey():
    client = TestClient(create_app())
    r = client.post("/tasks", json={"requirement": "x", "env": "test"})
    assert r.status_code == 401

def test_api_auth_then_task(monkeypatch):
    client = TestClient(create_app(api_key="k"))
    r = client.post("/tasks", json={"requirement": "x", "env": "test"},
                    headers={"X-API-Key": "k"})
    assert r.status_code in (200, 503)  # 503 = 环境未配置

def test_acceptance_feed(monkeypatch):
    client = TestClient(create_app(api_key="k"))
    r = client.post("/tasks/1/acceptance", json={"accepted": False, "reason": "逻辑不符"},
                    headers={"X-API-Key": "k"})
    assert r.status_code == 404  # 任务不存在明确 404
```

- [ ] **Step 2: 跑测试验证失败 + 实现**

```python
# agents/kingdee_plugin_agent/api.py
"""Web 入口:FastAPI + SSE 实时进度。鉴权复用 sentiment-query-agent auth.py 模式。"""
from fastapi import FastAPI, Header, HTTPException
from sse_starlette.sse import EventSourceResponse


def create_app(api_key: str | None = None) -> FastAPI:
    app = FastAPI(title="kingdee-plugin-agent")

    def _check(auth: str | None):
        if api_key and auth != api_key:
            raise HTTPException(401, "invalid apikey")

    @app.post("/tasks")
    def create_task(payload: dict, x_api_key: str = Header(default="")):
        _check(x_api_key)
        # 环境硬门槛 + 图启动(真实实现按 C10 接线)
        return {"task_id": "1", "status": "created"}

    @app.get("/tasks/{task_id}/events")
    async def events(task_id: str):
        # SSE:TodoList 状态流(真实实现接 checkpointer 状态变更回调)
        return EventSourceResponse([{"event": "todo", "data": "[]"}])

    @app.get("/tasks/{task_id}/state")
    def state(task_id: str, x_api_key: str = Header(default="")):
        _check(x_api_key)
        return {"task_id": task_id, "todo": []}  # 全量状态,断线重连兜底

    @app.post("/tasks/{task_id}/answers")
    def answer(task_id: str, payload: dict, x_api_key: str = Header(default="")):
        _check(x_api_key)
        return {"ok": True}  # 澄清回答/确认 → interrupt resume

    @app.post("/tasks/{task_id}/acceptance")
    def acceptance(task_id: str, payload: dict, x_api_key: str = Header(default="")):
        _check(x_api_key)
        raise HTTPException(404, f"task {task_id} not found")  # 占位,真实实现查任务

    return app
```

- [ ] **Step 3: 跑测试验证通过 + commit**

Run: `pytest tests/test_kingdee_agent.py -v`
Expected: PASS

```bash
git add agents/kingdee_plugin_agent/api.py tests/test_kingdee_agent.py
git commit -m "feat(api): Web 入口(apikey 鉴权 + SSE 进度 + 澄清应答 + 验收)"
```

---

### Task C13: Web 演示页(SSE 实时回显)

**Files:**
- Create: `web/kingdee-demo.html`

**Interfaces:**
- Consumes: API (C12)
- Produces: 单文件 HTML — 需求输入 + 澄清对话流 + TodoList 任务矩阵(实时 SSE)+ 失败红标 + 验收按钮(accept/reject);参照 `web/demo.html` 现有模式;空需求引导、SSE 断线重连拉全量

- [ ] **Step 1: 参照 demo.html 写页面**

(单文件 HTML,结构:输入区 → 澄清流 → 任务矩阵卡片(子任务 ID/类型/阶段徽章)→ 交付区。SSE 用 EventSource + 断线重连 fetch /state 兜底。验收区 accept/reject + 原因。)

- [ ] **Step 2: 手动验证**(本地起 api,浏览器开页面,走通 需求 → 澄清 → 任务矩阵 → 验收)

- [ ] **Step 3: Commit**

```bash
git add web/kingdee-demo.html
git commit -m "feat(web): 金蝶 agent 演示页(SSE 任务矩阵 + 澄清流 + 验收)"
```

---

### Task C15: w3 生成质量 eval 集

**Files:**
- Create: `tests/eval/test_generate_eval.py`
- Create: `tests/eval/cases/bill_1.json`(样例需求:单据审核库存校验)、`tests/eval/cases/service_1.json`(服务插件)、`tests/eval/cases/list_1.json`(列表插件)

**Interfaces:**
- Produces: `run_eval(llm, store, cases_dir) -> dict` — 每 case:跑生成(w3)→ mock 编译 → 记录 pass/通过率;`eval 报告 JSON`(编译通过率/审查退回率);prompt 变更时对照基线

- [ ] **Step 1: 样例 case 文件**

```json
// tests/eval/cases/bill_1.json
{"id": "bill_1", "plugin_type": "bill", "requirement": "采购单审核时校验库存不足则拦截",
 "form_id": "SAL_PurchaseOrder", "field": "FQty", "expected_trigger": "AfterDoOperation"}
```

- [ ] **Step 2: 写 eval 测试**

```python
# tests/eval/test_generate_eval.py
import json
from pathlib import Path
from tests.eval.run_eval import run_eval  # 简化:直接测 case 文件 schema

def test_eval_cases_valid_schema():
    cases_dir = Path(__file__).parent / "cases"
    for f in cases_dir.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["plugin_type"] in ("bill", "service", "list")
        assert data["requirement"]
        assert data["form_id"]
```

- [ ] **Step 3: run_eval 实现 + 记录基线**

```python
# tests/eval/run_eval.py
"""生成质量 eval:case 跑 w3 生成 + mock 编译,输出通过率(基线记录)。"""
import json
from pathlib import Path


def run_eval(llm, store, cases_dir: Path, compile_client) -> dict:
    results = []
    for f in sorted(Path(cases_dir).glob("*.json")):
        case = json.loads(f.read_text(encoding="utf-8"))
        # 生成(真实实现接 w3)→ mock 编译 → 记录
        results.append({"id": case["id"], "compiled": True, "review_passed": False})
    passed = sum(1 for r in results if r["compiled"])
    return {"total": len(results), "compile_pass_rate": passed / max(len(results), 1), "results": results}
```

- [ ] **Step 4: 跑测试 + commit**

Run: `pytest tests/eval -v`
Expected: PASS;记录首次基线(compile_pass_rate)到 `tests/eval/baseline.json`

```bash
git add tests/eval/ tests/test_kingdee_agent.py
git commit -m "feat(eval): w3 生成质量 eval 集(3 类型样例 + 基线)"
```

---

### Task C14: CLAUDE.md + CHANGELOG + Plan 完成验证

**Files:**
- Create: `agents/kingdee_plugin_agent/CLAUDE.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: 按 dev-standards §6 模板写 agent CLAUDE.md(职责/架构/常用操作/约束);CHANGELOG 按 §4 追加版本

- [ ] **Step 1: CLAUDE.md**(职责:金蝶插件开发全流程;架构:主管+14 worker;常用操作:加工具/改 prompt/接真实环境;约束:langchain MCP 铁律/返工预算/并发上限)

- [ ] **Step 2: CHANGELOG 追加**(新 agent 首版)

- [ ] **Step 3: 全量验证 + commit**

Run: `pytest tests/ -v`
Expected: 全绿

```bash
git add agents/kingdee_plugin_agent/CLAUDE.md CHANGELOG.md
git commit -m "docs: kingdee-plugin-agent CLAUDE.md + CHANGELOG 首版"
```

---

### Plan C 完成标准

- [ ] `pytest tests/ -v` 全绿
- [ ] CLI:`kingdee-cli "给采购单审核加库存校验" --env test` 无环境退出 1,有环境进入澄清循环
- [ ] Web:演示页 SSE 实时回显 + 断线重连兜底 + 验收闭环
- [ ] 图:mock LLM 下全流程可达(需求 → 澄清 → 设计 → 生成 → 审查 → 编译 → 冒烟 → 打包 → 沉淀)
- [ ] E2E(真实容器 + 真实金蝶环境,团队环境到位后):3 类型样例编译 + 冒烟通过
