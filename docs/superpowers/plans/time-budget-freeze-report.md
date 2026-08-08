# 时间预算 + 需求版本冻结 实现报告

设计出处:`docs/superpowers/specs/2026-08-08-kingdee-plugin-agent-design.md` §8(错误处理表两行)
commit:`feat(state): 时间预算(全流程 30min 总闸)+ 需求版本冻结(spec_version + 确认后不可变)`

## 1. 时间预算(全流程 30min 图级总闸)

### 三级预算现状核对

| 级别 | 设计 | 现状 | 本次动作 |
|---|---|---|---|
| 单轮编译 ≤2min | CompileClient timeout=120s | 已覆盖(终审时已实现) | 无代码,文档说明 |
| 单任务编译阶段 ≤15min | 5 轮 × 120s | 天然 ≤10min(5 × 120s),w5 内部覆盖 | 无代码,文档说明 |
| **全流程 ≤30min** | 图级总闸 | **未实现** | **本次实现** |

### 实现

- `graph/state.py`:`PIPELINE_TIME_BUDGET = 1800.0` 常量 + `TaskState.started_at: float = 0.0`。
  - `started_at=0.0` 语义 = 未设置/旧状态兼容:decide 不判定(falsy 短路),旧测试全部不受影响。
  - **存于 state 而非 thread_id**:interrupt 挂起 → `Command(resume=...)` 恢复时 checkpointer 恢复同一份 state,started_at 不重置;thread_id 复用也天然隔离(CLI/API 每次唯一)。新任务创建时写 `time.time()`。
- `cli.py` / `api.py`:建任务初始 state 增加 `"started_at": time.time()`(两处注释说明不重置语义)。
- `graph/supervisor.py` `decide()`:新增第 5 步确定性检查 —— `started_at` 距今 >1800s 且有未交付工作 → 剩余子任务标记 failed → `fail:时间预算耗尽`(与返工预算同语义,剩余产物留 TodoList 摘要)。
  - 顺序:依赖失败传递 → 全部 delivered→finish → 有 failed→fail → 返工预算 → **时间预算** → 派发 → LLM/ask_user。all-delivered 与 failed 检查在前,预算检查只作用于"有活没干完"的挂起状态。
- LLM 路径:`_summary_table` 增加"时间预算: 已用 Xs / 总闸 1800s"行,LLM 决策上下文可感知总闸并选择 fail(LLM 的 fail 本就放行)。

### 测试

- `test_task_state_time_and_version_defaults`:started_at 缺省 0.0、spec_version 缺省 1。
- `test_supervisor_decide_time_budget_exceeded`:started_at = now-2000 → `fail:时间预算耗尽`,剩余标记 failed。
- `test_supervisor_decide_zero_started_at_normal`:started_at=0 → 正常派发 `run:A1`(不误杀)。
- `test_supervisor_llm_context_includes_time_budget`:摘要表含"时间预算/总闸 1800s"。
- `test_api_task_creation_sets_started_at`:API 建任务后 handle.state 的 started_at > 0。

## 2. 需求版本冻结(spec_version + 确认后不可变)

### 既有行为验证(先查后锁)

**API 端点逐个核对(api.py)**:

- `POST /tasks/{id}/answers`:只投递 interrupt resume。确认后图中只可能产生 ask_user 类型 interrupt(w1 的 question/confirm 分支被 `not spec_confirmed` 门控),ask_user 的 resume 在 w1 节点只追加 `user_feedback`,**不写 requirement_spec**。
- `POST /tasks/{id}/acceptance`:只收 `accepted`/`reason`,record_acceptance 记验收结论 + 拒绝原因喂 w7 经验库,**无 spec 写路径**。
- `POST /tasks`(建任务):新建任务,不动既有任务。
- 无 PUT/PATCH 端点。

**图内 requirement_spec 写路径**:全图只有 `_confirm_and_split` 写 requirement_spec(经 w1 确认分支,确认前可改 spec —— 符合"重新澄清(未确认)可以改 spec"),确认后无任何节点再写。冻结在结构上已成立。

**结论**:既无松动路径,但"无写路径"是隐式契约,缺显式锁。本次加锁防回归:

### 实现

- `graph/state.py`:`TaskState.spec_version: int = 1`(缺省 1;确认时显式盖章,语义 = 本任务冻结的是第 1 版需求)。
- `agent.py` `_confirm_and_split`:确认通过时更新 `"spec_version": 1`(确认即冻结的唯一盖章点);`_send_payload` 携带 spec_version(w6 打包用)。
- `api.py` `TaskHandle.deliver_answer` **冻结锁**:`spec_confirmed=True` 时,当前 interrupt 类型非 `ask_user` → 409「需求已确认并冻结,不能修改需求;如要修改请开新任务」。question/confirm 只出现在确认前(未确认可继续改 spec),确认后任何非 ask_user 输入都被拒 —— 锁住"answers 不得被解释为 spec 修改路径",防未来加端点/改路由回归。
- 交付记录盖章:`w6_package.py` 把 `spec_version` + `requirement_spec`(冻结 spec 快照)传入 deliverable;`tools/package.py` 写 `records/spec.json`(`{"spec_version": N, "requirement_spec": {...}}`),交付包可审计"这份包对应哪版需求"。交付包契约 5 条目 → 6 条目(源码/DLL/部署说明/设计/审查/spec)。
- 验收端点无需改动(仅 accepted/reason,已确认安全)。

### 测试

- `test_task_state_time_and_version_defaults`:spec_version 缺省 1。
- `test_graph_full_flow_to_finish`:确认后 `spec_version == 1` 盖章。
- `test_graph_midrun_ask_user_interrupt`(增强):确认后 ask_user 回答前后 `requirement_spec` 快照一致(不改 spec,只记 user_feedback)。
- `test_api_answers_frozen_after_confirmation_non_ask_user`:TaskHandle 级 —— 确认后 interrupt 类型为 question → 409 冻结(断言 detail 含"冻结")。
- `test_api_answers_ask_user_ok_after_confirmation`:确认后 ask_user 恢复放行。
- `test_package_worker_stamps_spec_version`:w6 打包产物 records/spec.json 含 spec_version=1 + spec 快照。
- `test_package_build`(test_kingdee_api.py 更新):契约 6 条目 + spec.json 内容断言。

## 3. 文档

- `agents/kingdee_plugin_agent/CLAUDE.md` 约束新增 2 条:时间预算(三级预算 + 总闸语义 + started_at 生命周期)、需求版本冻结(确认即冻结 + answers 锁 + 开新任务)。
- `docs/kingdee-plugin-agent/tech.md`:decide 顺序补第 5 步;TaskState 表补 started_at/spec_version 行;错误表 #22 更新为三级预算 + 图级总闸、新增 #23 需求版本冻结(原 23-25 顺延 24-26);§10.2 预算表补单任务编译阶段、全流程时间预算两行。
- `docs/kingdee-plugin-agent/manual.md`:answers 端点表补冻结 409;FAQ 新增 Q8(改需求 → 开新任务)、Q9(超时 → 30min 总闸);交付物解读补 records/spec.json。
- `CHANGELOG.md`:v1.9.0 条目。
- 注:三份文档与 CLAUDE.md 原无这两项"未实现"标注(此前只写了编译级时间预算,未提总闸/冻结),本次属补齐实现 + 文档,无残留"未实现"表述。

## 4. 测试结果

`pytest tests/ -q`:**172 passed**(164 基线 + 8 新增;test_kingdee_agent.py 新增 8 项含增强,test_kingdee_api.py 1 项契约更新),2 warnings 为既有(starlette deprecation / torch CUDA 环境)。

## 5. 自审

- **不误杀**:started_at=0.0 falsy 短路,旧状态/旧测试(全部 TaskState 构造未传 started_at)不受影响;预算检查排在 all-delivered/failed 之后,交付完成不触发。
- **resume 不重置**:started_at 是普通字段,无节点写它,checkpointer 恢复原值 —— 与 rework_budget_left 同一持久化模式;msgpack 序列化 float/int 无兼容问题。
- **Send payload 通道**:spec_version 加入 `_send_payload`(w6 需要);started_at 不进 payload(worker 不需要,supervisor 经全量 state 拿到)。
- **冻结锁无竞态**:`_run_loop` 先持锁写 `handle.state` 再 `_set_interrupt`(持锁置 waiting),deliver_answer 看到 waiting=True 时 state 必已更新,spec_confirmed 判断可靠。
- **409 后重试**:冻结 409 不消费 interrupt,客户端可继续以正确类型恢复。

## 6. 顾虑

- **spec_version 恒为 1**:v1 确认后不可改,版本号只作"冻结标记 + 交付审计",无 v2 场景;将来若开放"确认后补充澄清"(需设计放宽),spec_version 语义需升级为递增。
- **时间预算按挂钟计时**:包含用户思考/澄清等待时间(interrupt 挂起期间 clock 继续走)。设计表意即全流程 ≤30min(含交互),符合"超预算强制升级"意图;若后续要"只计执行时间"需改为累计执行时长,超出本次范围。
- **API 线程模型**:建任务后后台线程立即 invoke,started_at 在初始 state 写入后再启动线程,无毫秒级语义问题。
- **测试耗时**:API 冻结测试直接构造 TaskHandle 单元级验证(不跑全图),避免 30s 等待路径拖慢套件;图级冻结语义由 `test_graph_midrun_ask_user_interrupt` 全图覆盖。
