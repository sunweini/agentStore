# 下发模板补验收标准/上限字段 + 设计 14→8 worker 偏差同步 — 报告

日期:2026-08-09
范围:kingdee-plugin-agent(设计 §3 worker 结构偏差 + §5.1 下发模板字段缺失)
提交:feat(contract): 下发模板补验收标准/上限字段(Subtask + w4 对照 + per-subtask 重试上限)

---

## Finding 1:设计 14 worker → 实际 8 worker(排查结论:无功能缺口,补齐测试覆盖 + 同步设计文档)

### 排查过程与结论

设计 §3 为 14 worker:w1 + w2×3(w2a/b/c)+ w3×3 + w4×3 + w5 + w5.5 + w6 + w7。
实现为 8 worker:每角色一个 worker,w2/w3/w4 的类型分支经 `TYPE_PROMPTS` 配置表路由到
`skills/<skill>/references/<type>.md`(bill/service/list 三套,文件存在且非空,25~41 行/份)。

**职责覆盖验证(14 → 8 逐项对照)**:

| 设计 14 worker | 实现 8 worker | 覆盖方式 |
|---|---|---|
| w1 | w1 | 1:1 |
| w2a/b/c 单据/服务/列表设计 | w2 | `TYPE_PROMPTS` → design-builder/references/{bill,service,list}.md |
| w3a/b/c 单据/服务/列表生成 | w3 | `TYPE_PROMPTS` → code-generator/references/* + 类型模板 templates/<type>/template.cs(基类按类型写死) |
| w4a/b/c 单据/服务/列表审查 | w4 | `TYPE_PROMPTS` → code-reviewer/references/* |
| w5 / w5.5 / w6 / w7 | w5 / w5_5 / w6 / w7 | 1:1 |

**类型知识传递路径检查(两条路径都完整)**:
- w2 LLM 路径:base + branch 文本拼进系统提示(DesignWorker._execute);骨架路径(llm=None):
  骨架设计文本 = `类型 + {base + branch}` 全文,类型要点完整进骨架。✓
- w3 LLM 路径:base + branch 拼系统提示;骨架路径:渲染**类型专属模板**(bill→AbstractBillPlugIn /
  service→AbstractOperationServicePlugIn / list→AbstractListPlugIn),类型知识由模板承载。✓
- w4 LLM 路径:base + branch 拼系统提示;骨架路径为模板占位符检测(类型无关,无需类型知识)。✓
- 未知插件类型防护:w2/w4 `TYPE_PROMPTS.get()` + None 检查 → ERROR 上报;w3 由 `load_template`
  ValueError 兜底 → ERROR(不裸 KeyError)。✓
- RAG 过滤:w2/w3 guide 检索按 `subtask.plugin_type` 相等过滤。✓

**测试覆盖检查与补缺**:既有测试断言了 TYPE_PROMPTS 三键齐全 + 指向 references 文件
(`test_worker_type_branches_read_from_skill_references`),但 w2/w3/w4 的**执行测试只跑 bill**。
补 `test_w2_w3_w4_execute_all_three_types`(bill/service/list 参数化,llm=None 确定性路径):
断言类型要点文本进 w2 骨架设计、类型基类进 w3 代码、占位符全渲染、w4 干净代码 Approved。

**结论:无功能缺口,无修复性改动;仅设计文档同步 + 测试覆盖补全。**

### 设计文档同步(§2 / §3 / §3.2 / §4)

- §2 编排行:14 worker → 8 worker + TYPE_PROMPTS 类型配置表。
- §3 标题与正文:改写为「1 主管 + 8 worker(已实现;设计原 14 worker 按类型拆 → 实现收敛为
  8 worker + 类型配置表)」,标注**实现偏差记录**,附 14→8 等价表;保留 w2a/b/c 命名作等价说明。
- §3.2:14 → 8 个 worker 共用统一基类,类型差异走配置表(单源 skill references)。
- §4 数据流:③④⑤ 的 w2x/w3x/w4x 改为 w2/w3/w4 + 类型分支说明。

project.md / manual.md 本就一致(8 worker),无需改动;tech.md §1/§2 亦已一致。

## Finding 2:§5.1 下发模板未实现(验收标准/上限字段缺) — 已实现

### 改动清单

1. **`graph/state.py` Subtask 新增 3 字段**:
   - `acceptance_criteria: str = ""`(该环节可验证的完成标准;空 = 按需求确认摘要验收)
   - `max_rework: int = 0`(本子任务退回上限,0 = 全局默认 GLOBAL_REWORK_BUDGET)
   - `rework_count: int = 0`(已发生返工轮次,主管统一维护)
   全部带默认值 → 既有位置构造 `Subtask("A1","bill","x",[],"pending")` 与旧 checkpointer
   状态(msgpack 反序列化缺字段)均兼容。

2. **`w1_requirement.py` 拆解 schema 与落值**:
   - `PlanItem` 新增可选 `acceptance_criteria` / `max_rework`(LLM 从确认规格归纳)。
   - LLM 路径透传,未给 → 确定性兜底:`acceptance_criteria="按需求确认摘要验收"`(确认摘要即验收基准,
     呼应设计 §7 artifact 验收点)、`max_rework=max(int(v or 0), 0)`(负值钳 0)。
   - `_split_fallback`(llm=None)同样填默认值,两条路径一致。

3. **`agent.py` 契约传递与上限裁决**:
   - `_send_payload`:todo 全量快照随 Send 分支下发,Subtask 新字段经 `_as_state` 的
     `_SUBTASK_FIELDS`(import 时由 `Subtask.__dataclass_fields__` 推导)自动随行 —— 无需改 payload;
     TaskState 级新字段才需显式加(CLAUDE.md 已注明该边界)。
   - 新增 `_bump_rework(sub)`:rework_count+1,超过 max_rework(>0)→ 返回 True(fail)。
   - `_advance_status` 三条返工路径统一接入:w4 Needs fixes / w5 编译超限 / w5_5 冒烟失败
     → 超上限标 failed 而非 needs_rework;未扣预算的 BLOCKED(基础设施缺失)路径不受影响。
   - **与全局预算的协同(已文档化)**:子任务上限是环节级更早触发的闸门,返工轮次已实际发生
     仍上报 rework_events 照扣全局预算(≤3 轮是任务级最终防线),两者叠加不抵消。

4. **`w4_review.py` + `prompts/w4_review.md` 审查对照验收标准**:
   - `_llm_review` context 新增 `acceptance_criteria` 键;非空时追加 human 提示
     「需求符合性是最高优先级审查项,未满足项按 severity 规则列入 findings(缺需求行为视为 Critical)」;
     空时不追加提示(不误导)。
   - 确定性审查路径(占位符检测)不受影响。

### 测试(新增 9 项,全套 212 项,基线 203)

- `test_subtask_contract_fields_default`:新字段默认值。
- `test_w1_split_llm_acceptance_fields_pass_through` / `test_w1_split_fallback_acceptance_fields`:
  LLM schema 透传 + 两条路径兜底值。
- `test_w4_review_context_includes_acceptance_criteria` / `..._criteria_empty_default`:
  context 含验收标准与对照提示;空标准不追加提示。
- `test_graph_subtask_max_rework_fails`:max_rework=1 → 第 2 次审查退回时子任务 failed(不再重工),
  rework_count=2、全局预算照扣(3→1),action=fail。
- `test_w2_w3_w4_execute_all_three_types`(参数化 ×3):Finding 1 的测试覆盖补全。
- 既有全局返工预算测试(默认 max_rework=0 走全局闸门)原样通过,语义未变。

### 文档

- 设计文档 §5.1:标注实现状态 —— 验收标准/上限两项 ✅ 已落地,实际机制 = Subtask 字段 +
  `_send_payload` 快照(TASK_ID/TYPE/INPUT/RAG 由阶段映射与产物路径隐式携带)。
- tech.md:§2.1 Subtask 表补 3 字段 + 下发模板落地机制注;§10.2 轮次上限表补子任务退回上限行。
- agents/kingdee_plugin_agent/CLAUDE.md:任务契约字段列表、改任务契约操作说明、约束
  (子任务上限与全局预算协同)。
- CHANGELOG:v1.12.0。

## 文件变更

```
M CHANGELOG.md
M agents/kingdee_plugin_agent/CLAUDE.md
M agents/kingdee_plugin_agent/agent.py            (_bump_rework + _advance_status 上限裁决)
M agents/kingdee_plugin_agent/graph/state.py      (Subtask +3 字段)
M agents/kingdee_plugin_agent/graph/workers/w1_requirement.py (PlanItem + 透传/兜底)
M agents/kingdee_plugin_agent/graph/workers/w4_review.py      (审查 context 对照验收标准)
M agents/kingdee_plugin_agent/prompts/w4_review.md
M docs/kingdee-plugin-agent/tech.md
M docs/superpowers/specs/2026-08-08-kingdee-plugin-agent-design.md (§2/§3/§3.2/§4/§5.1)
M tests/test_kingdee_agent.py
```

## 自审

- 数据流闭环:w1 拆解填字段 → Send 快照带字段 → w4 消费验收标准 → 返工事件消费上限;
  api.py `_subtask_dict`(vars)自动把新字段带进 SSE 输出,无需改动。
- 兼容性:新字段全带默认值;`_as_state` 重建旧状态缺字段走默认;msgpack 序列化 int/str 安全。
- 上限语义:max_rework=1 允许 1 轮返工,第 2 轮触发失败(「超过」即 fail,与模板「退回轮次」语义一致);
  三条返工路径(w4/w5/w5_5)统一,未来新增返工路径需同步接入 `_bump_rework`(CLAUDE.md 已注明)。
- 测试注入约定未破坏:只注入 LLM/外部服务,不 mock LangGraph。

## 顾虑

- **并行返工边缘**:两个并行子任务同超步各自触发 max_rework 失败时,`rework_events` 通道
  last-wins 会丢一次预算扣减(v1 已知近似,与既有 rework_events 合并语义一致,不影响 failed 判定)。
- **LLM 拆解填 max_rework 的信任边界**:上限值来自 LLM 输出(经 int() 钳 0),未设硬上限;
  若真实需求需要强约束,可在 w1 拆解后由主管统一 clamp(如 ≤ GLOBAL_REWORK_BUDGET)。
- **验收标准依赖确认摘要的显式性**:llm=None 路径默认「按需求确认摘要验收」,属弱标准
  (摘要文本即验收基准);真实环境联调时建议在 w1 拆解 prompt 中引导 LLM 把决策/假设提炼成
  可验证的完成标准。
- 设计文档 §3 的「实现偏差记录」属事后同步,后续新 worker 设计应直接按 8 worker + 配置表写。
