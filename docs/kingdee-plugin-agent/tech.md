# kingdee-plugin-agent 技术文档

> 面向开发者:架构、任务契约、worker 详解、skill 体系、知识库、错误处理、安全、部署、测试、性能预算。
> 本文以代码为事实依据;与 `agents/kingdee_plugin_agent/CLAUDE.md`(架构/约束/债务)保持一致,代码变化时同步更新。设计出处:`docs/superpowers/specs/2026-08-08-kingdee-plugin-agent-design.md`。

## 1. 架构:LangGraph 图结构

### 1.1 图拓扑(agent.py `build_graph`)

LangGraph 1.2.10 循环图,状态 schema 为 dataclass `TaskState`。节点:supervisor(主管)+ dispatcher(派发)+ w1(交互)+ w2..w7(8 个 worker 中 7 个非交互 worker 各注册为分支节点)。

```
START
  │
  ▼
supervisor ────conditional route(route 函数,机械映射,终态判定在 Supervisor.decide)───►
  │ run:<sid>   → dispatcher
  │ ask_user    → w1(交互节点,interrupt 挂起)
  │ finish|fail → END
  │
  ▼
dispatcher ── 返回 Command(update=…, goto=[Send(worker, payload), …])──► worker ×N(并行 ≤3)
  │        (batch = 就绪批:各子任务生命周期下一阶段,pending 依赖须满足;
  │         Send 入参 = payload 快照 dict,非全量 state —— 通道字段在 _send_payload 显式携带)
  │        (无可用派发 → 防御性 Command(goto="supervisor") 回主管重决策)
  ▼
worker 节点(w2/w3/w4/w5/w5_5/w6/w7)── 静态回边 ──► supervisor(回环,直至 finish/fail)
w1 ── 静态边 ──► supervisor
```

要点:

- **主管循环**:每个 worker/w1 完成后都回主管,主管重决策(依赖失败传递 → 终态检查 → 就绪批派发 → LLM 决策)。`rework_events` 由分支上报、主管统一扣减预算(`rework_budget_left` 是普通字段,主管唯一写者,防并行覆盖)。
- **Send 分支**:节点返回 `Command(update=…, goto=[Send(...)])` 并行派发,并发 ≤ `MAX_PARALLEL=3`;分支结果按通道 reducer 合并(`todo` 按 id,见 state.py `_merge_todo`)。**dispatcher 不加静态边**(实测:节点返回 Command(goto=[Send...]) 时静态边会同时生效,导致主管在分支执行中被再次调度)。
- **interrupt 语义**:`interrupt(value)` 挂起 → 结果含 `__interrupt__`;`Command(resume=answer)` 恢复。挂起节点 resume 时**整体重跑**,payload 必须由 state 确定性得出(w1 问题清单/确认摘要均预先存 state,不依赖 LLM 重算)。
- **recursion_limit 是运行时 config 参数**(`graph.invoke(..., config={"recursion_limit": N})`),不是 compile 参数;预算公式见 §10.1。
- **checkpointer**:缺省 `MemorySaver`(interrupt 必需);生产可换 `AsyncSqliteSaver`。CLI/API 每次运行用唯一 `thread_id` 隔离会话。
- **终态**:`Supervisor.decide` 顺序(确定性优先,LLM 只做无法机械判定时的选择):
  1. 依赖失败传递(pending 依赖者 → failed)
  2. 全部 delivered → `finish`
  3. 存在 failed → `fail:存在失败子任务`
  4. 返工预算耗尽且有未交付工作 → 剩余标记 failed → `fail:返工预算耗尽`
  5. 有就绪批 → `run:<sid>`(LLM 存在时选优,确定性兜底派批首)
  6. 无可派发 → LLM 决策(ask_user/finish/fail)或确定性 `ask_user`
- **LLM 动作校验(防幻觉)**:run 的 sid 必须 ∈ 当前就绪集合,否则回退派批首;澄清期(todo 空)LLM 幻觉 finish 会被 `_all_delivered` 门控拦下回退确定性兜底;fail 放行(主管裁量)。

### 1.2 依赖注入(build_graph)

所有外部依赖可注入(测试传 fake,生产缺省):

| 参数 | 缺省 | 用途 |
|---|---|---|
| `store` | `ArtifactStore()` | 产物落盘(JSON 文件库) |
| `compile_client` | 从 `COMPILE_SERVICE_URL` 构造 | w5 编译 |
| `rag` | None(= 无检索降级) | w2/w3 检索 |
| `standards` | None | w4 规范库注入 |
| `api_client` | 从 `KD_*` 环境构造 | 冒烟验证 |
| `llm` | `get_chat_model()`;显式 None = 确定性骨架路径 | 全部 LLM 调用 |
| `smoke_client` | 基于 api_client/环境构造,无环境则 None | w5.5 |
| `experience` | None(= 跳过) | w2 历史坑 / w5 修复检索 / w7 沉淀 |
| `package_builder`/`output_dir` | `PackageBuilder(data/kingdee-deliverables)` | w6 打包 |
| `checkpointer` | MemorySaver | interrupt 必需 |

## 2. 任务契约

### 2.1 Subtask(子任务)

| 字段 | 说明 |
|---|---|
| `id` | 子任务 id(白名单 `^[A-Za-z0-9_-]+$`,防路径穿越) |
| `plugin_type` | `bill` \| `service` \| `list` |
| `title` | 标题(w2 经验检索双信号) |
| `deps` | 依赖子任务 id 列表(依赖须 packaged/delivered 才派发;dep 不存在视为可选) |
| `status` | 生命周期状态(见 2.3) |
| `design_path` / `code_path` | 设计文档 / Plugin.cs 落盘路径(worker 经 artifact_key 写回) |
| `compile_errors` | 编译错误列表 `[{code, message, experience?}]` |
| `review_verdict` | `Approved` \| `Needs fixes`(w4 确定性裁决) |
| `review_path` | w4 审查报告 review.json 路径 |
| `report` | worker 上报 dict(见 2.4) |

### 2.2 TaskState(图状态)

| 字段 | reducer | 说明 |
|---|---|---|
| `requirement_spec` | — | 需求 spec(decision + assumptions,确认后拆解) |
| `todo` | 按 id 合并 | 子任务池(并行分支各自回写自己的子任务) |
| `rework_budget_left` | 普通字段(默认 3) | 返工预算,主管唯一写者;分支只报 `rework_events` 不直写(并行同步写普通通道会 InvalidUpdateError) |
| `rework_events` | 替换合并 | 分支报 `[1]`,主管下超步应用并写 `[]` 清空;同一步两分支同时报事件 last-wins 丢一次(v1 接受该近似,并行返工属边缘场景) |
| `final_deliverable` | last-wins | 最近一个交付包(兼容既有契约) |
| `final_deliverables` | 追加去重 | 多子任务交付包合并(v1 逐包) |
| `environment` | — | 环境配置(冒烟 form_id 等) |
| `action` | — | 主管动作 `run:<sid>` \| `ask_user[:<问题>]` \| `finish` \| `fail[:<原因>]` |
| `dispatch_id` | — | Send 分支输入通道(分支不写回) |
| `user_feedback` | — | 用户反馈/补充 |
| `clarify_questions/answers/feedback`、`clarify_round`、`confirm_attempts`、`spec_confirmed` | — | w1 澄清状态机 |

### 2.3 子任务生命周期状态机

```
pending → in_progress → design_done → gen_done → review_done
        → compile_done → smoke_done → packaged → delivered(终态)
        → needs_rework(退回 w3 重新生成,扣返工预算)
        → blocked(等用户,经 w1 问用户)/ failed(终态)
```

状态 → 下一阶段 worker 映射(`STATUS_TO_WORKER`):

| 状态 | 下一 worker | 状态 | 下一 worker |
|---|---|---|---|
| pending | w2(设计) | smoke_done | w6(打包) |
| design_done | w3(生成) | packaged | w7(沉淀) |
| gen_done | w4(审查) | blocked | w1(问用户) |
| needs_rework | w3(重新生成) | in_progress / delivered / failed | 不再派发 |
| review_done | w5(编译) | compile_done | w5_5(冒烟) |

### 2.4 上报契约(worker → 主管)

`WorkerBase.run(state, subtask)` 统一实现:调 `_execute` 拿 `{status, artifact_key, path, evidence, concerns}`,把 `path` setattr 进 `subtask.<artifact_key>`(design_path/code_path/review_path/final_deliverable),`subtask.report = {"worker": name, **result}`,返回上报文本。

图包装器 `_advance_status` 按 worker 名 + report.status 推进生命周期:

- w2:`ERROR` → failed,否则 design_done
- w3:`ERROR` → failed,否则 gen_done
- w4:`ERROR` → failed;`review_verdict == "Needs fixes"` → needs_rework(返工事件);否则 review_done
- w5 / w5.5:`BLOCKED` 且本轮扣了预算(编译超限/冒烟失败)→ needs_rework;`BLOCKED` 未扣预算(基础设施缺失)→ failed(重工无意义,防无限重试);否则 compile_done / smoke_done
- w6 → packaged;w7 → delivered

### 2.5 审查裁决契约(w4)

LLM 只产 findings(severity/line/issue/依据/修法),**裁决由确定性规则计算**,防 LLM 伪造裁决:

```
存在 Critical 或 Important → Needs fixes(退回 w3)
仅 Minor 或无误           → Approved
```

w3 确定性骨架保证全部 `{{TOKEN}}` 渲染,防 w4 把未渲染占位符误判 Critical;w4 的 LLM 失败/llm=None 骨架 = 模板占位符残留 → Critical。

## 3. Worker 详解(8 个)

### w1 需求澄清(交互节点,不参与 Send 派发)

- **职责**:一次一问澄清(≤`MAX_ROUNDS=10` 轮)→ 确认摘要(已确认决策 + 假设)→ 拆子任务(LLM `PlanOutput`,失败确定性兜底按 `spec.plugin_types` 缺省 bill)→ spec.json + plan.json 落盘。
- **交互流**(agent.py w1 节点驱动):首轮 `generate_questions` 一次生成问题清单存 state(interrupt resume 不重算)→ 逐问 `interrupt({"type": "question", ...})` → 问题问完 `interrupt({"type": "confirm", ...})` → 确认 `_confirm_and_split`;不确认则补充记入假设,`confirm_attempts >= 2` 时带假设强制收口(防无限循环)。中途 `ask_user` 走同节点挂起。
- **LLM 调用**:`structured_with_skill(QuestionsOutput/PlanOutput)` + skill 摘要注入;llm=None 时 1 个默认问题 / 确定性拆分。
- **降级**:LLM 故障 → 默认问题/确定性拆分,不阻塞。

### w2 设计(DesignWorker)

- **输入**:需求 spec + 子任务标题 + w2_design.md prompt + 类型分支要点(单源在 `skills/design-builder/references/<type>.md`)+ RAG 检索(guide 按 `plugin_type` 相等过滤 + api_ref)+ 经验库历史坑(标题双信号 `search_related(title, title, k=3)`,verified 优先)。
- **输出**:`DesignOutput(design_markdown)` → `design_path`。
- **降级**:LLM 失败/llm=None → 确定性骨架(类型 + 要点文本);RAG/经验库故障 → 空命中继续;未知插件类型 → ERROR 上报 → 子任务 failed(不裸 KeyError)。

### w3 代码生成(GenerateWorker)

- **输入**:设计文档 + 类型模板(`templates/<type>/template.cs`,占位符 `{{NAMESPACE}}`/`{{CLASS_NAME}}`/`{{BUSINESS_LOGIC}}`,基类写死在模板)+ w3_generate.md + 类型分支要点(code-generator skill references)+ guide 检索。
- **输出**:`CodeOutput(code)` → `code_path`(Plugin.cs)。**模板优先,冲突以模板为准**。
- **降级**:LLM 失败/llm=None → `render_template` 渲染全部 3 个占位符(逐 token str.replace,不用 str.format,C# 字面大括号免疫),防 w4 判 Critical;设计产物缺失/未知类型 → ERROR → failed。

### w4 审查(ReviewWorker)

- **输入**:Plugin.cs + 规范库整库注入(`standards.inject_text()`,8000 token 预算)+ w4_review.md + 类型分支要点(code-reviewer skill references)。
- **输出**:`ReviewOutput(findings)` → `review_path`(review.json)+ 确定性 `review_verdict`。
- **降级**:LLM 失败/llm=None → 骨架 findings(未渲染 `{{TOKEN}}` → Critical);规范库未注入(standards=None)→ 无规范上下文,LLM 凭知识 + 模板基线审查。

### w5 编译修复(CompileWorker)

- **流程**:① 编译前 `health()` 探测(容器未起 → BLOCKED,**不计编译轮次**);② 循环编译至多 `MAX_COMPILE_ROUNDS=5` 轮:成功 → compile_errors 清空 + DONE;失败 → 错误记 `subtask.compile_errors`,按错误码 `search_related(code, message, k=2)` 命中附注 experience,再让 LLM 依 w5_compile.md + 错误(含经验附注)改写代码写回重编(**必须真实改写,禁止原样重提交**);③ 5 轮仍失败 → `rework_budget_left -= 1` 后 BLOCKED(needs_rework 恒映射 w3,退回 w3 重新生成;预算耗尽则由主管 fail)。
- **降级**:编译服务 503(`CompileUnavailableError`)/超时/连接失败(`httpx.HTTPError`)→ BLOCKED 不计轮次不扣预算;经验库故障 → 无附注继续;LLM 修复失败 → 原样重提交受轮次上限约束。

### w5.5 部署冒烟(SmokeWorker)

- **职责**:运行时验证(assembly 加载 + FormId→plugin 映射),防"编译过跑不起来"。
- **流程**:`smoke_client.deploy_and_verify(Path(code_path), form_id)`(form_id 取 `state.environment`);失败 → `rework_budget_left -= 1` 后 BLOCKED(needs_rework 恒映射 w3,退回 w3 重新生成);成功 → DONE。
- **降级**:冒烟客户端未配置(KD_BASE_URL 缺失)→ BLOCKED 但不扣预算 → 图包装器标记 failed(基础设施缺失走重工无意义)。⚠️ 端点 `/metadata/verify` 为初始契约占位,真实环境可用后按部署 API 调整。

### w6 打包(PackageWorker)

- **职责**:子任务产物 → 交付包 zip。**v1 按子任务逐包交付**(文件名带子任务 id,并行打包互不覆盖);图上包装器把包路径追加进 `state.final_deliverables`,`final_deliverable` 保留最近一个。v2 合并为单一 zip。
- **产物**:`deliverable-{sid}-{ts}.zip` = `source/Plugin.cs` + `bin/Plugin.dll`(dll_path 存在时;**当前 w6 恒传空串,DLL 未入包**)+ `deploy.md`(部署说明)+ `records/design.json` + `records/review.json`(**均为空占位 `{}`:打包器未接线设计/审查记录**)。

### w7 知识沉淀(DistillWorker)

- **职责**:把子任务 compile_errors 全量 `experience.propose(err.code, "", err.message, "w7 沉淀,待人工验证")` 写入经验库(proposed 态)。**确定性代码,无 LLM 调用,不绑定 load_skill**。
- **降级**:沉淀失败 → `DONE_WITH_CONCERNS`,不阻塞交付(记待沉淀队列)。

## 4. Skill 体系(6 个)

### 4.1 结构

| skill | 绑定 | 形态 |
|---|---|---|
| `requirement-clarify` | w1 澄清(LLM 可主动调用) | SKILL.md + 类型模板直放 skill 目录(bill/service/list.md,老形态) |
| `design-builder` | w2 设计 | SKILL.md + `references/{bill,service,list}.md` |
| `code-generator` | w3 生成 | SKILL.md + `references/{bill,service,list}.md` |
| `code-reviewer` | w4 审查 | SKILL.md + `references/{bill,service,list}.md` |
| `compile-fixer` | w5 编译修复 | SKILL.md + `references/errors.md`(**纯方法论**:错误分类/根因分析/检索策略/修复纪律;具体错误映射不写 skill,单一来源经验库 seed + w7 沉淀) |
| `knowledge-steward` | w7 沉淀 + 人工维护(不绑定工具) | SKILL.md + `references/distillation.md`(沉淀质量标准)+ `references/maintenance.md`(维护手册)+ 检索路由速查表 |

### 4.2 渐进式披露 + load_skill 机制(skills/loader.py)

- **摘要层**:`_AVAILABLE_SKILLS` 6 个摘要;`skill_summary()`(摘要 JSON)仅注入 w1 澄清问题生成(`generate_questions` 系统提示,帮助 LLM 选题),其余 worker 不注入摘要;`SKILL_HINT`(load_skill 提示)逐节点注入(w1 拆解、w2~w5),告诉 LLM 可调 `load_skill(skill_name)` 拿方法论;supervisor 决策无 skill 注入。
- **工具层**:`load_skill` 按 agent → common 顺序查找 skill 目录,返回 `{skill, summary, references(name→content 映射,模板正文全量交付 —— agent 的 LLM 没有文件工具,只给文件名等于没给), scripts(恒空), content}`;非法 skill 名返回 error JSON 与可用列表。
- **绑定形态**:`structured_with_skill(schema, messages)` = `with_structured_output(schema, tools=[load_skill], include_raw=True)`(**必须用官方 tools 参数;bind_tools 后再 with_structured_output 会经 `__getattr__` 委派丢失 tools**,loader docstring 注明)。模型回合 1 调 load_skill → 执行喂回 ToolMessage → 回合 2 出 schema JSON,最多 2 回合;parsed 仍空 → None → worker 确定性骨架降级。不传 strict(worker 输出 schema 含默认值字段,OpenAI strict json_schema 禁止默认值)。脚本/fake LLM(无 bind_tools)自动跳过绑定。
- **⚠️ 未线上验证**:该绑定形态未对真实 DeepSeek 线上验证;首次真实环境联调先跑 w1 `generate_questions` smoke,被 API 拒绝则改用 sentiment 的 JSON Mode 模式(见 CLAUDE.md 约束)。

### 4.3 prompt 变薄原则(单源)

w2/w3/w4 的类型分支要点**不在 prompts/,单源在 `skills/<skill>/references/<type>.md`** —— worker 的 `TYPE_PROMPTS` 与 `load_skill` 交付读同一份文件,改方法论只改 skill 一处,不写回 prompts。prompts/ 只留各 worker 的通用节点 prompt(每文件 4~18 行,w1 澄清 prompt 最薄仅 4 行,其余 10~18 行)。注意:prompts/ 与 skill references 拼进系统提示后都经 ChatPromptTemplate f-string 解析,含 `{...}` 的 JSON 样例必须转义 `{{...}}`(dev-standards §7.2);作为 load_skill JSON 交付时保持文本原样。

## 5. 知识库(common/rag.py)

### 5.1 四库设计

| 库 | 存储 | 消费者 | 检索方式 |
|---|---|---|---|
| `api_ref`(API 参考) | Chroma 向量库 | w2/w3 | 混合检索(默认 bm25_weight=0.5;0.7 为知识库路由表约定,未接线) |
| `guide`(开发指南) | Chroma 向量库 | w2/w3 | 混合检索(按 plugin_type 相等过滤) |
| `experience`(经验库) | Chroma 向量库 | w2 历史坑 / w5 修复 / w7 写入 | `ExperienceStore.search_related`(k=2~3) |
| `standards`(规范库) | 纯 markdown | w4 审查 | 整库注入 `inject_text(limit=8000 token)`,超限标注"请调用 guide_fallback 检索"(⚠️ guide_fallback 返回的是 guide 库命中,不是 standards 内容,不得误当作规范检索) |

### 5.2 混合检索

`hybrid_search(collection, query, k, bm25_weight, filter)` = 内建 Okapi BM25(k1=1.5, b=0.75, 纯 Python 实现,langchain_community 未安装)+ Chroma 向量(L2)+ **加权倒数排名融合**(与官方 EnsembleRetriever 语义一致):`score(doc) = Σ w_i / (rank_i + c)`,c=60,rank 从 1 起。`filter` 支持简单 `{key: value}` 相等匹配(两通道均生效;向量通道透传 chromadb where 语法)。

⚠️ **分数方向**:`search()`(纯向量)返回 L2 距离,**越小越相关**;`hybrid_search` 返回 RRF 融合分,**越大越相关**,两者不可跨方法比较。

嵌入:BGE `BAAI/bge-small-zh-v1.5`(`HuggingFaceEmbeddings`,全局单例,~2GB 内存)。

### 5.3 经验库两态 + 去重(ExperienceStore)

- 状态机:`proposed ──verify()──▶ verified`;`archive()` 归档(文档与向量不动,仅元数据 status 更新,chromadb `Collection.update(ids, metadatas)` 路径)。
- **签名去重**:`signature = "code|file_pattern"`;`propose()` 先按 `filter={"signature": sig}` 精确查重,同签名已存在直接返回,防 w7 幻觉污染。
- `search_related(error_code, message, k)` 仅返回 proposed/verified:proposed 标 `confidence="unverified"`,verified/种子(无 status 元数据,视为人工策展等同已核验)标 `confidence="verified"`;archived 等其余状态被过滤。
- **种子**:`seed/compile_errors.json` 7 条(CS0246/CS0103/CS0234/CS1061/CS0506/CS0115 等),`seed_load` CLI 幂等灌入(签名去重,"种子灌入完成:新增 N 条")。

### 5.4 检索路由表

| 阶段 | 库 | 检索式 | 用途 |
|---|---|---|---|
| w2 设计 | guide(按 plugin_type 过滤)+ api_ref + experience | `search_related(title, title, k=3)`(设计阶段无错误码,标题当 code/message 双信号) | 指南参数化;历史踩坑 → 设计规避(verified 优先、仅供参考非必须满足) |
| w3 生成 | guide(按 plugin_type 过滤) | hybrid_search | 字段/操作/API 签名参数化 |
| w4 审查 | standards | 整库注入(非检索) | 规范逐条对照 + API 抽查(凭模型知识,未接 api_ref 检索,属后续增强) |
| w5 修复 | experience | `search_related(code, message, k=2)` | 错误命中 → 附注修复建议 |
| w7 沉淀 | experience | `propose(code, "", message)` | 新坑入 proposed 态 |

## 6. 错误处理(场景表)

| # | 场景 | 处理 |
|---|---|---|
| 1 | 环境硬门槛:KD_* 缺失 | CLI 缺 KD_BASE_URL → exit 1 不进图;API 4 项全校验,缺任一 → 503 并点明缺项 |
| 2 | 编译服务不可用(容器未起/503/超时/连接失败) | w5 先 health() 探测;BLOCKED,**不计编译轮次、不扣返工预算** → 图包装器标记 failed |
| 3 | 编译失败(有错误列表) | 错误记 compile_errors + 经验库检索附注 + LLM 改写重编,≤5 轮 |
| 4 | 编译 5 轮仍失败 | 扣返工预算 → BLOCKED → needs_rework → 退回 w3 重新生成 |
| 5 | 冒烟失败(assembly 未加载/FormId 映射错) | 扣返工预算 → BLOCKED → needs_rework → 退回 w3 重新生成 |
| 6 | 冒烟客户端未配置 | BLOCKED 不扣预算 → failed(基础设施缺失走重工无意义) |
| 7 | 全局返工预算耗尽(≤3 轮) | fail,剩余子任务标记 failed,输出 TodoList 摘要(部分产物 + 原因) |
| 8 | LLM 结构化输出失败(畸形 JSON/异常) | 返回 None → worker 确定性骨架降级(w1 默认问题 / w2 骨架 / w3 渲染 token / w4 占位符 Critical / w5 原样重编译轮次兜底),不中断图 |
| 9 | 依赖失败传递 | pending 依赖者 → failed(派发前做,防把失败依赖的依赖者派发出去) |
| 10 | LLM 幻觉 finish(澄清期) | `_all_delivered` 门控拦截,回退确定性兜底(防零交付误报成功) |
| 11 | LLM 决策非法 run sid | 校验 sid ∈ 就绪集合,否则回退派批首 |
| 12 | 未知插件类型 | w2/w3/w4 转 ERROR 上报 → 子任务 failed(不裸异常上抛) |
| 13 | 产物缺失(设计/代码不存在) | w3/w4 转 ERROR → failed |
| 14 | 金蝶 API 429/5xx/超时 | 指数退避重试 2 次(1s/2s),仍败 → `KingdeeApiUnavailable` |
| 15 | 金蝶 API 响应非 JSON/业务失败 | 统一按不可用处理,不泄漏裸 ValueError |
| 16 | RAG 检索故障 | try/except 降级空命中,不阻塞设计/生成 |
| 17 | 经验库检索故障 | 无附注继续,不阻断编译循环 |
| 18 | w7 沉淀失败 | DONE_WITH_CONCERNS,不阻塞交付 |
| 19 | 验收拒绝原因沉淀失败 | 不阻塞验收(记录 warning 日志) |
| 20 | 路径穿越 | ArtifactStore 子任务 id 白名单 `^[A-Za-z0-9_-]+$`,非法抛 ArtifactStoreError |
| 21 | 并行写同一通道冲突 | todo/rework_events/final_deliverables 走 reducer;预算主管唯一写者;dispatch_id 分支不写回 |
| 22 | 时间预算 | 编译单轮 ≤120s(httpx timeout);msbuild 后端 120s 超时;answers 端点等图挂起 ≤30s 否则 409 |
| 23 | 澄清无限循环 | 逐问 ≤10 轮;确认最多 1 次补充,仍不确认带假设强制收口 |
| 24 | 任务中断(CLI) | stdin EOF → 提示并 exit 1;API 中断后内存任务存储重启即丢(v1 债务),重建任务重跑 |
| 25 | 非法 skill 名 | load_skill 返回 error JSON + 可用列表 |

## 7. 安全

- **apikey 鉴权**(api.py):`X-API-Key` 头;来源优先级 `create_app(api_key=...)` 显式参数 > 环境 `KINGDEE_API_KEY` > `API_KEYS_JSON` 首个 key(复用 sentiment auth.py 数据源);未配置有效 key 默认拒绝(401)。⚠️ 已知债务:字符串直接比较,未用 `secrets.compare_digest`。
- **环境凭证**:`KD_USERNAME/KD_PASSWORD/KD_DATA_CENTER` 等只经 `.env` 注入(`.env` 在 .gitignore,不提交),随请求体携带到金蝶 WebAPI(该 API 的登录方式之一)。
- **CORS**:演示放开 `allow_origins=["*"]`(web/kingdee-demo.html 跨域访问),注释标注"生产按需收紧"。
- **路径白名单**:ArtifactStore 子任务 id 正则白名单,拒绝 `..`/`/` 等路径穿越;load_skill 名必须 ∈ `_AVAILABLE_SKILLS` 才读盘。

## 8. 部署

### 8.1 docker-compose 拓扑

```yaml
services:
  compile-service:
    build: ./compile_service
    ports: ["8000:8000"]
    volumes:
      - ./compile_service/build/references:/app/references
  # api(agent 入口)待启用:
  #   build: .
  #   ports: ["8080:8080"]
  #   environment: [COMPILE_SERVICE_URL=http://compile-service:8000]
  #   volumes: [./data/kingdee-rag:/data/kingdee-rag]
```

首版仅编译容器;**compose 文件在 Plan C(API 入口)落地后未更新,api 服务仍处于注释未启用状态**(本地开发直接 `uvicorn agents.kingdee_plugin_agent.api:create_app --factory` 起 API)。

### 8.2 compile_service(编译容器)

- **后端选择**(`server.py::_backend_from_env`):`COMPILE_SERVICE_REQUIRES_DLLS=1` → 真实 `MsbuildCompiler`(从 `REFS_DIR` 目录 glob `*.dll`,缺 DLL 构造即抛 `CompileUnavailableError`,服务不启动,标记"DLL 未到位");否则 `MockCompiler`(预设规则表,开发/CI 用,**不当质量门**)。
- **真实后端**:临时目录生成 csproj(net48)+ 引用 DLL → `msbuild`(120s 超时)→ `error_parser` 解析(正则 `File.cs(12,5): error CS0123: msg`,`(code,file)` 去重,级联洪水上限 10 条)→ 进程 returncode 非零即使无错误行也判失败。
- **接口**:`GET /health` → `{"status": "ok"}`;`POST /compile {code, project_name}` → `{success, raw_output, duration_ms, errors:[{file, line, code, message, is_fatal}]}`;后端不可用 → 503(客户端 `CompileUnavailableError`)。
- **Dockerfile**:基础镜像 `mcr.microsoft.com/dotnet/framework/sdk:4.8-windowsservercore-ltsc2022`(Windows 容器,msbuild);`COPY build/references/ → /app/references`;`ENV COMPILE_SERVICE_REQUIRES_DLLS=1`;`docker-entrypoint.sh` 为 mono/Linux 基础镜像预留 DLL 校验入口。⚠️ 金蝶 BOS 编译在 Linux 容器兼容性(mono/.NET 兼容层或 Windows 容器)待验证。
- **客户端**(tools/compile_client.py):`COMPILE_SERVICE_URL`(缺省 http://localhost:8000),timeout 120s(10s 会让真实编译超时误判)。

### 8.3 数据目录(gitignore)

`data/kingdee-rag/`(Chroma 向量库 + 经验库)/ `data/kingdee-artifacts/`(子任务产物)/ `data/kingdee-deliverables/`(交付包)全部 gitignore,不提交。

## 9. 测试

- **规模**:164 项全过(CHANGELOG v1.8.0 记录):`tests/test_kingdee_agent.py`(86 项,图全链路/CLI/worker/skill)+ `tests/test_kingdee_api.py`(8 项)+ test_compile_service / test_rag / test_templates。
- **注入约定**:只注入 LLM/外部服务(`build_graph(llm=None)` + fake 编译/冒烟),**不 mock LangGraph 本身**;CLI 测试 monkeypatch 模块级 `build_graph`。
- **图可达性测试**:覆盖 主管循环、依赖拓扑、并行派发(≤3)、返工预算、finish/fail 终态、interrupt 澄清流、Send 分支 payload 快照、reducer 合并、防"supervisor↔dispatcher 空派发忙循环"。
- **eval 集**(tests/eval/):`cases/*.json`(3 类型样例)+ `run_eval.py`(w3 生成 → mock 编译 → 事件断言)+ `baseline.json`(确定性基线,`llm=None`,提交/CI 对比回归);评估用 `EVAL_MOCK_RULES`(CS9990 未渲染占位符残留 —— 默认 MockCompiler 规则对真实插件误报,勿用);`trigger_ok` 断言覆盖方法声明(裸子串会命中设计摘要注释,误报)。
- **E2E 门(未达成)**:真实容器编译 3 类型样例插件各一通过 —— 依赖团队金蝶 BOS DLL 到位(里程碑 1 启动门);mock 编译服务仅作开发期辅助,不当质量门。

## 10. 性能与预算

### 10.1 recursion_limit 公式

```
default_recursion_limit(n) = 100 + 20 × n
```

运行时 config 参数(非 compile 参数),调用方按子任务数显式传入:CLI/API 澄清期不知道子任务数,按上限 10 给足 → 300 超步(覆盖澄清 + 全流水线 + 返工重跑)。预算依据:复合任务(7 子任务)实测需 ~120 超步;旧 50+10×n 在 n=10 时 150 < 实际 ~160 → GraphRecursionError,返工即溢出;100+20×n 留舒适余量。

### 10.2 并发与轮次上限

| 项 | 值 | 说明 |
|---|---|---|
| 并行子任务 | ≤3(`MAX_PARALLEL`) | 防 DeepSeek 限流/超时风暴 |
| 全局返工预算 | ≤3(`GLOBAL_REWORK_BUDGET`) | 总重新生成轮数,超限硬失败 |
| 编译轮次 | ≤5(`MAX_COMPILE_ROUNDS`) | 服务不可用不计轮次 |
| 澄清轮次 | ≤10(w1 `MAX_ROUNDS`) | 逐问上限;确认 ≤2 次尝试 |
| 单轮编译 | ≤120s | httpx timeout + msbuild timeout |
| answers 等待 | ≤30s | 超时 409 让客户端重试 |
| recursion_limit | 100+20n(调用点按 10 → 300) | 运行时 config |

### 10.3 token 控制

- supervisor 决策上下文:requirement_spec 截断 2000 字符。
- w1 澄清:需求截断 1500 字符;问题 ≤10 个。
- w4 规范注入:8000 token 预算(中文 ~1.5 字/token),超限截断并标注 guide_fallback。
- RAG/经验检索 k=2~3;w3 骨架设计要点截断 200 字符。
- 嵌入模型 BGE 单例 ~2GB 内存(资源评估见设计 §13)。

## 11. 已知债务与未验证项(与 CLAUDE.md 同步)

**v1 已知债务**(上线前需决策):API 任务存 `app.state.tasks` 进程内内存,重启即丢、无持久化/恢复;API 每任务一个后台 daemon 线程,无线程池/并发闸门;apikey 字符串比较非 timing-safe;TaskState/Subtask 经 checkpointer(msgpack)序列化,升级 LangGraph 版本需验证兼容(api.py `_subtask_dict` 已兼容实例/dict 两形态);CLI `--env` 只进 requirement_spec 未做环境级差异化;CLI 门控仅 KD_BASE_URL(API 已全校验 4 项)。

**未验证项**:线上 DeepSeek 验证 load_skill 绑定(见 §4.2);真实金蝶环境 WebAPI 端点/响应结构(当前为文档化初始契约占位,见 tools/kingdee_api.py 头部警告);E2E 启动门(真实容器编译 3 类型样例);Linux 容器 BOS 编译兼容性;规范库(standards)目录与文档导入尚未接真实资料。
