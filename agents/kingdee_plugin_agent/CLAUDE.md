# kingdee-plugin-agent 开发指南

金蝶云星空插件开发 Agent:输入自然语言需求,自动完成 澄清 → 设计 → 生成 → 审查 → 编译修复 → 冒烟 → 打包 → 沉淀 全流程,交付可部署的插件包(TodoList + 交付产物)。

## 本 agent 是什么

- 职责:把"给金蝶 X 单据加 Y 功能"的模糊需求,变成经过设计/审查/编译/冒烟的插件交付物;踩坑自动沉淀经验库,越用越准。
- 开发前必读:根目录 [CLAUDE.md](../../CLAUDE.md) 和 [docs/dev-standards.md](../../docs/dev-standards.md)(必须依据 langchain MCP 文档/API 开发)。

## 架构

1 主管 + dispatcher + 8 worker 的循环图,worker 经 `Send` 并行派发(并发 ≤3):

```
START → supervisor ──run:──→ dispatcher ──Send──→ w2/w3/w4/w5/w5.5/w6/w7(并行 ≤3)
          │                     │                        │
          └──ask_user──→ w1     └──finish|fail──→ END    └──→ supervisor(回环)
```

w1 是交互节点(interrupt 挂起,不参与 Send 派发);其余 worker 与 supervisor 各有静态回边。任务契约(设计 §5.1 下发模板落地):主管拆解需求为 Subtask(id, plugin_type, title, deps, status, **acceptance_criteria**(该环节可验证的完成标准,w4 审查对照用), **max_rework**(本子任务退回上限,0 = 全局默认 GLOBAL_REWORK_BUDGET), **rework_count**(已发生返工轮次,主管统一维护), design_path, code_path, dll_path, compile_errors, review_verdict, review_path, report),worker 只按 `dispatch_id` 处理自己那份,以 `report` dict 上报(状态 + 消息 + 产物路径)。冒烟链路:w1 确认时把目标单据 FormId 提取进 `state.environment["form_id"]`(PlanOutput.form_id 显式槽 + decisions 兜底,见 w1_requirement.extract_form_id),w5 编译成功时把后端产出 DLL 路径存 `subtask.dll_path`(mock 后端为空 → w5.5 跳过部署验证并显式标注,不拿源码冒充)。

| 文件 | 职责 |
|---|---|
| [agent.py](agent.py) | 图构建入口:`build_graph()`(依赖全可注入,测试传 fake;缺省 MemorySaver checkpointer)+ `default_recursion_limit()`(100+20×子任务数) |
| [cli.py](cli.py) | CLI 入口 `run_cli`:环境硬门槛(KD_BASE_URL)→ stdin 交互澄清循环 → TodoList 摘要 + 交付包路径 |
| [api.py](api.py) | Web 入口 `create_app()`:apikey 鉴权 + KD_* 4 项硬门槛(503)+ SSE 实时流 + 澄清应答 + 验收 |
| [graph/state.py](graph/state.py) | 任务契约 TaskState/Subtask + 常量(GLOBAL_REWORK_BUDGET=3 / MAX_PARALLEL=3,todo 按 id reducer 合并) |
| [graph/supervisor.py](graph/supervisor.py) | 主管节点:依赖拓扑/就绪批派发/返工预算唯一写者/终态判定(finish/fail) |
| [graph/workers/base.py](graph/workers/base.py) | worker 统一基类:`run(state, subtask)` 契约,产物经 store 落盘,report 上报 |
| [graph/workers/w1..w7](graph/workers/) | 8 worker:w1 需求澄清(interrupt 逐问 + 确认摘要)/ w2 设计 / w3 生成 / w4 审查 / w5 编译修复 / w5.5 冒烟 / w6 打包 / w7 知识沉淀 |
| [prompts/](prompts/) | 节点 prompt 与代码分离:`supervisor.md` + 每 worker 一个;w2/w3/w4 的类型分支要点不在 prompts,单源在 `skills/<skill>/references/`(worker TYPE_PROMPTS 直接读 skill 文件) |
| [tools/](tools/) | 外部能力:compile_client(编译服务)/ kingdee_api(金蝶元数据)/ smoke_client(冒烟)/ package(打包) |
| [store/artifact_store.py](store/artifact_store.py) | 产物落盘(JSON 文件库:spec/plan/代码/审查/编译/交付) |
| [skills/](skills/) | 方法论 skill 目录(6 个):`requirement-clarify`(w1 澄清,老形态模板直放 skill 目录)+ `design-builder`/`code-generator`/`code-reviewer`/`compile-fixer`(w2-w5 方法论,SKILL.md + `references/` 子目录类型要点)+ `knowledge-steward`(知识库全生命周期:沉淀方法论 + 维护手册 + 检索路由速查表,`references/` = distillation.md + maintenance.md);`compile-fixer/references/errors.md` 为**纯方法论**(分类/根因/检索/纪律),具体错误映射不进 skill,单一来源经验库(seed + w7 沉淀);`loader.py` 的 load_skill 工具(渐进式披露:摘要启动加载,w1-w5 经 `structured_with_skill` 绑定,LLM 主动调方法论,2 回合上限;w7 为确定性代码无 LLM,不绑定,knowledge-steward 供人工维护/未来 LLM 化参照)+ `skill_summary()` + `SKILL_HINT` |
| [seed/](seed/) | 经验库种子数据(compile_errors.json)+ 灌入脚本 seed_load |
| [templates/](templates/) | 三类型插件模板(bill/list/service),w3 生成参照 |

## 常用操作

- **加 worker**:`graph/workers/` 新建 wN_xxx.py(继承 base.WorkerBase)→ `agent.py` 的 `workers` dict 注册 → `supervisor.py` 的 STATUS_TO_WORKER/状态机补状态迁移 → 按需加 prompt。
- **改 prompt**:`prompts/<name>.md`,节点内按名字加载;注意 ChatPromptTemplate 是 f-string 语法(JSON 样例 `{}` 转义 `{{}}`,见 dev-standards §7.2)。
- **改任务契约**:`graph/state.py` 的 Subtask/TaskState 字段 —— 加普通字段注意并行写冲突(用 reducer 或改由主管统一写);`Send` 分支入参是 payload 快照,新字段要在 `agent.py::_send_payload` 带上(`todo` 是全量快照,Subtask 新字段经 `_as_state` 的 `_SUBTASK_FIELDS` 自动随行,无需单独改 payload;TaskState 级新字段则要显式加进 `_send_payload` + `_as_state`)。
- **接经验库**:`build_graph(experience=...)` 一次注入,w2(设计历史坑参考)/w5(修复检索)/w7(沉淀)共享;w2 按子任务标题 `search_related(title, title, k=3)` 检索(title 同时充当 code/message 双信号,设计阶段无错误码),命中注入设计上下文"历史踩坑参考"段(verified 优先、仅作规避参考非必须满足),检索故障降级空命中不阻塞设计。
- **接真实金蝶环境**:`.env` 配 `KD_BASE_URL/KD_USERNAME/KD_PASSWORD/KD_DATA_CENTER` 4 项(硬门槛:CLI 缺 KD_BASE_URL exit 1;API 4 项全校验,缺任一 503);编译服务配 `COMPILE_SERVICE_URL`(缺省 http://localhost:8000,起 `docker-compose up`);API 鉴权配 `KINGDEE_API_KEY`。
- **改 skill**:`skills/<skill>/` 下 SKILL.md(方法论:目标/输入/流程/输出契约/踩坑)+ `references/`(类型要点;requirement-clarify 老形态模板直放 skill 目录,无 references/),`skills/loader.py` 的 `_AVAILABLE_SKILLS` 注册摘要(渐进式披露);**方法论只写进 skill,不要写回 prompts** —— w2/w3/w4 的 worker TYPE_PROMPTS 与 load_skill 都从 `skills/<skill>/references/` 取同一份内容(单源)。改 LLM 侧工具提示:loader 的 `SKILL_HINT`(每步注入)+ `structured_with_skill`(绑定形态:官方 tools 参数,勿用 bind_tools 再 with_structured_output —— `__getattr__` 委派会丢 tools,已在 loader docstring 注明)。注意:prompts/ 与 skill references 被 worker 拼进系统提示后都经 ChatPromptTemplate f-string 解析,含 `{...}` 的样例(如 JSON 契约)必须转义 `{{...}}`(dev-standards §7.2);作为 load_skill JSON 交付时保持文本原样 —— 转义是模板安全,不是内容变更。
- **跑测试**:`pytest tests/test_kingdee_agent.py -v`(图全链路 + CLI + API,确定性注入 llm=None + fake 编译/冒烟)+ `pytest tests/test_kingdee_api.py`;全量 `pytest tests/ -q`。
- **启动 CLI**:`python -m agents.kingdee_plugin_agent.cli "给采购单审核加库存校验" --env test`。
- **启动 API**:`uvicorn "agents.kingdee_plugin_agent.api:create_app" --factory --reload`(演示页 web/kingdee-demo.html)。

## 约束

- **langchain MCP 铁律**:开发前必须查 docs-langchain / reference-langchain MCP 确认 API 用法,禁止凭记忆写 API(见根 CLAUDE.md)。
- **返工预算**:`GLOBAL_REWORK_BUDGET = 3`(总重新生成 ≤3 轮),超限 → fail,剩余子任务标记 failed;失败收尾(设计 §8)由 `w6_fail` 节点产出"未完成"包 `deliverable-failed-<ts>.zip`(部分产物 + compile_errors + 退回意见 + 原因,route 里 fail → w6_fail → END),CLI 输出 TodoList 摘要 + 该包路径;预算由主管统一扣减(worker 只上报 rework_events,不直写,防并行覆盖)。**子任务级上限(设计 §5.1)**:`Subtask.max_rework`(0 = 全局默认)与 `rework_count` —— 返工事件时 `agent.py::_advance_status` 先 rework_count+1,超过 max_rework(>0)→ 该子任务 failed 而非 needs_rework(环节级更早触发的闸门);返工轮次已实际发生仍照扣全局预算,两者叠加不抵消(全局 ≤3 轮是任务级最终防线)。
- **并发上限**:`MAX_PARALLEL = 3`(send() 并行子任务 ≤3,防 DeepSeek 限流/超时风暴)。
- **编译轮次**:w5 循环编译至多 `MAX_COMPILE_ROUNDS = 5` 轮;编译服务不可用 → 报 BLOCKED,不算轮次不扣预算。
- **时间预算(设计 §8)**:单轮编译 ≤120s(CompileClient timeout)、单任务编译阶段 ≤15min(5 轮 × 120s 天然 ≤10min,由 w5 内部覆盖);全流程 ≤30min 图级总闸(`PIPELINE_TIME_BUDGET=1800.0`):`started_at` 距今超限且有未交付工作 → 剩余标记 failed → `fail:时间预算耗尽`;`started_at` 由 CLI/API 建任务时写入初始 state(存于 state 而非 thread_id,挂起 resume 不重置);0.0 = 未设置(旧状态兼容不判定)。
- **需求版本冻结(设计 §8)**:spec 确认(`spec_confirmed`)即冻结,`spec_version=1` 盖章,requirement_spec 此后无任何写路径 —— w1 只在未确认时构建/修改 spec,确认后中途问题(ask_user)的回答只记 user_feedback;API answers 确认后仅接受 ask_user 类型 interrupt 的恢复(其余 409「需求已确认并冻结」,防回归);修改需求须开新任务;w6 打包把 `spec_version` + 冻结 spec 快照写入交付包 `records/spec.json`(可审计)。
- **环境硬门槛**:无金蝶环境不进图 —— CLI 未配 KD_BASE_URL 直接退出;API 4 项缺失 503 并点明缺项;冒烟客户端未配置 → BLOCKED → failed(防无限重试循环)。
- **知识沉淀两态**:w7 写入经验库走 proposed/verified 两态 + "code|file_pattern" 签名去重(防幻觉污染);验收拒绝原因同通道(proposed 态,sha256 摘要入签名,失败不阻塞验收)。
- **interrupt 语义**:挂起节点 resume 时整体重跑,payload 必须由 state 确定性得出(不依赖 LLM 重算);恢复用 `Command(resume=answer)`。
- **recursion_limit 是运行时 config 参数**,不是 compile 参数;按子任务数给足(100+20×n,澄清期按上限 10 算 —— 旧 50+10×n 在 n=10 时 150<实际需求 ~160,复合任务触发 GraphRecursionError)。
- **w1 澄清上限**:逐问 interrupt ≤10 轮;确认摘要最多再确认 1 次,仍不确认带假设强制收口(防无限循环)。
- **测试注入约定**:只注入 LLM/外部服务(build_graph(llm=None) + fake 编译/冒烟),不 mock LangGraph 本身。
- **load_skill 绑定未线上验证**:w1-w5 的 `structured_with_skill` 用 tools + json_schema response_format 组合绑定 load_skill,未对真实 DeepSeek 线上验证;首次真实环境联调时先跑 w1 generate_questions smoke,若被 API 拒绝改用 sentiment 的 JSON Mode 模式(`bind_tools([load_skill], strict=True).bind(response_format={"type": "json_object"})` + 手动 2 回合循环)。

## v1 已知债务(上线前需决策)

- **内存任务存储**:API 任务存于 `app.state.tasks`(进程内),重启即丢,无持久化/恢复。
- **API 线程无并发上限**:每任务一个后台线程,无线程池/并发闸门,流量大时需限流。
- **apikey 非 timing-safe**:`x_api_key != effective_key` 直接字符串比较,未用 `secrets.compare_digest`。
- **msgpack 反序列化警告**:TaskState/Subtask 经 checkpointer(msgpack)序列化,升级 LangGraph 版本时 dataclass 字段/嵌套 dict 需验证兼容(api.py `_subtask_dict` 已兼容 Subtask 实例/dict 两种形态)。
- **`--env` 部分消费**:CLI/API 的 env 值进 `requirement_spec` + `state.environment["env_name"]`(节点可感知),未做环境级差异化处理(单环境 v1)。
- **CLI 门控仅 KD_BASE_URL**:CLI 只校验 KD_BASE_URL(API 已全校验 4 项),单环境 v1 约定。
