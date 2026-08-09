# 版本更新说明(CHANGELOG)

项目:agentStore — 基于 LangChain/LangGraph 的多步骤任务 Agent 组
仓库:https://github.com/sunweini/agentStore

---

## v1.14.0 — 2026-08-09(kingdee-plugin-agent:RAG 导入管线 + guide/api_ref 集合灌入)

### 新增功能

- **RAG 导入管线 `tools/ingest.py`**(URL/目录双入口 + CLI,零新增依赖):
  - `ingest_url(url, collection, title="")`:httpx 抓取(30s 超时 + 浏览器 UA)→
    stdlib html.parser 提取正文(剔除 script/style/nav/header/footer 噪音)→
    行级样板剔除(分享/收藏/评论/翻页/导航类)→ 代码感知分块 → RagClient 入库
    (metadata: source/title/collection),返回新增 chunk 数;
  - `ingest_dir(dir, collection)`:递归 *.md,自动去 YAML frontmatter,相对路径
    作 source,单文件失败跳过继续,**全部失败才报错**(不静默全跳过);
  - `code_aware_chunk(text, max_chars=1500)`:段落边界切块;代码围栏(```)
    **无论多长整体独占一个 chunk,绝不在围栏内部切分**(未闭合围栏也保留);
    超长段落按句末标点(。！？!?;；)兜底切分;
  - `normalize_title(url, html=None)`:&lt;title&gt; → 首个 &lt;h1&gt; → URL 尾段
    三级回退,站点名后缀自动剥离;
  - **幂等是"去重式"而非同步式**:按 source + 文本查重,同 source 且**内容未变**
    重跑新增 0;内容变更后重跑会新增、新旧版本并存 —— 编辑已灌入文档须先删旧
    重灌:`--delete-source <source> --collection <库>`(删除该 source 全部条目)
    再重灌;
  - **&lt;pre&gt; 代码块缩进保留**:HTML 提取按 pre 感知处理 —— 代码行原样
    保留缩进/结构,非代码行折叠空白;浏览/赞赏计数等**动态行**(两次抓取数值
    不同)按样板剔除,保证同 URL 重跑文本稳定;
  - CLI:`--url <URL>`(可重复)/ `--dir <目录>` / `--seed-internal` /
    `--delete-source <source>` + `--collection api_ref|guide|experience`,
    `--data-dir` 可改数据目录;单 URL 失败打印明确原因(HTTP 状态/超时/无正文)、
    全部失败退出码 1。

### 集合灌入(data/kingdee-rag,gitignored;2026-08-09 实跑,重跑新增 0)

- **guide 71 chunks / 27 源**:内部 skill 7 份 SKILL.md + 14 份 references
  (design-builder / code-generator / code-reviewer / compile-fixer /
  knowledge-steward / requirement-clarify,51 chunks)+ 金蝶官方 6 页(BOS 平台
  知识地图、星空 BOS 平台简介、熊说金蝶 BOS 知识库、BOS FAQ 精选、收款单扩展
  实操、AI 辅助二开,20 chunks);
- **api_ref 4 chunks / 3 源**:金蝶官方 3 页(星空企业版开发笔记 —— 含
  BusinessDataServiceHelper/DBServiceHelper 用法、WebAPI 多选基础资料、WebAPI
  系统集成主题);
- 模板类(`templates/*.cs`)不入库 —— 代码模板由 w3 直接使用,无需检索。

### 测试

- 新增 `tests/test_ingest.py` 25 项(全套 212 → **237**):代码围栏跨段落整体
  保留/超长围栏不切分/未闭合围栏保留、长段落句末切分无内容丢失、HTML 噪音
  (script/nav/分享收藏)剔除 + **&lt;pre&gt; 缩进保留**、ingest_dir tmp 目录
  入库可检索 + frontmatter 剔除 + 去重幂等、**编辑后重跑重复 → delete_source
  删旧重灌干净**、ingest_url mock HTTP 入库 + HTTP 错误明确消息、
  **fetch_html 真实异常映射(超时/HTTP 状态/网络错误 → IngestError)**、
  CLI --dir 可运行 / 单 URL 失败退出 1 / 多 URL 部分失败继续 / --delete-source
  / 无参数退出 2。

### 文档

- **knowledge-steward SKILL.md 维护手册**:文档导入步骤改走 RAG 导入管线
  (命令示例 + 去重式幂等语义)。
- **knowledge-steward references/maintenance.md §2**:文档导入分步重写为
  ingest CLI(单页/批量目录/--seed-internal/--delete-source 形态 +
  --data-dir),明确"编辑已灌入文档 = 静默重复,须删旧重灌"纪律;注明
  plugin_type 元数据缺口(外部导入文档暂不支持类型过滤检索,待办)。
- **manual.md**:新增 §1.3 灌入 RAG 知识库(命令 + 已灌内容清单 + 抽查方法),
  后续小节顺延编号。
- **project.md**:§5.2 待办"RAG 内容"更新 —— guide/api_ref 已接真实资料
  (内部 skill + 官方 9 页,检索冒烟通过);剩余 standards 规范库目录与
  plugin_type 元数据扩展。

---

## v1.13.0 — 2026-08-09(kingdee-plugin-agent:E2E 门达成 —— 真实金蝶环境编译全通,部署/种子/文档同步)

### 新增功能

- **旧式 csproj 兼容 Framework MSBuild(无 VS 环境)**:`compile_service/backends/msbuild.py` 由 SDK 风格 csproj(需 VS 15+)改为生成 **ToolsVersion 4.0 旧式 csproj** —— 兼容 .NET Framework 自带 MSBuild(`C:\Windows\Microsoft.NET\Framework64\v4.0.30319\MSBuild.exe`),配合 .NET Framework 4.8 Developer Pack(参考程序集)编译 `TargetFrameworkVersion` 目标,无需安装 Visual Studio。
- **msbuild_path 自动探测**:`default_msbuild_path()` 优先 PATH 中的 msbuild(VS 环境),兜底 Framework 自带路径;`MSBUILD_PATH` 环境变量可显式覆盖(`server.py::_backend_from_env` 接线)。
- **target_framework 可配**:`TARGET_FRAMEWORK` 环境变量(默认 `v4.8`,需 Developer Pack 参考程序集);编译超时 120s → **180s**(msbuild 首次冷启动较慢)。
- **DLL persist 时序修复**:编译成功后 DLL 字节**在 TemporaryDirectory 退出前读取**(Windows 上退出后延迟读会 FileNotFoundError),再落盘服务端留存目录。

### 修复

- **三类型模板真实编译修复(真实金蝶 DLL 反射验证,commit 51e5468)**:bill/list 模板补 `using System;`(EventArgs 找不到 CS0246);`AfterDoOperationEventArgs` 在 `Kingdee.BOS.Core.DynamicForm.PlugIn.Args`(bill 补 using);service 模板删除**不存在的** `using Kingdee.K3.Core.ServiceHelper;` 假引用(类型不存在),基类 `AbstractOperationServicePlugIn` 在 `Kingdee.BOS.Core.DynamicForm.PlugIn`。
- **E2E 门达成(里程碑 1 启动门 ✅)**:bill/service/list 三类型样例插件在 **Windows Server 2016 金蝶服务器 + 金蝶 WebSite\bin 真实 DLL + .NET 4.8 DevPack + Framework MSBuild** 环境全部编译通过并产出 DLL —— 真实编译后端首次端到端验证通过,不再是 mock 质量门。

### 测试

- 全套 212 项全过;种子 7 条 → **10 条**(新增 MSB3274/MSB3275/CS0246(EventArgs)真实踩坑种子),seed_load 幂等断言更新(7 → 10,二次灌入仍为 0)。

### 文档

- **新增 `docs/kingdee-plugin-agent/windows-deployment.md`**:Windows 编译服务部署手册 —— 前置(Windows Server 2016+ / Python 3.10 / git clone)、.NET 4.8 Developer Pack、金蝶 DLL 采集(WebSite\bin,授权注意)、start_compile.bat + schtasks 保活、health + 真实编译验证、故障排查(端口/500 日志/MSB3274-3275/mock-vs-real 判别)、与 Ubuntu agent 连接(COMPILE_SERVICE_URL)。
- **种子踩坑(compile_errors.json)**:MSB3274/3275(引用程序集框架高于编译目标 → 引用被静默跳过,TARGET_FRAMEWORK 提到 v4.8)+ CS0246 EventArgs(缺 using System)。
- **manual.md**:部署节(Windows 编译服务部署步骤 + 端口冲突换端口)+ FAQ 增补(端口被金蝶占/编译 500 看 E:\uv.log/MSB3274-3275 提 TARGET_FRAMEWORK)+ 限制节更新(E2E 门已达成)。
- **tech.md**:§8.2 msbuild 后端更新(旧式 csproj 理由 / msbuild_path 探测 / target_framework 可配)+ §9 E2E 门状态 ✅(环境注明)+ §11 未验证项移除 E2E 门与 Linux 容器兼容性(实际采用 Windows 原生部署,容器方案保留)。
- **project.md**:里程碑 E2E 门 ❌待 DLL → ✅ 已达成(注明环境);待办更新(剩真实金蝶 WebAPI 凭证联调 + RAG 内容)。
- **设计文档 §13**:E2E 门状态更新;Linux 容器兼容性条目备注(实际采用 Windows 原生部署,容器方案保留)。
- **skill 措辞**:design-builder / code-generator SKILL.md"模板为团队验证基准"类措辞更新为"三类型模板已真实环境编译验证(Windows + 金蝶 BOS DLL)";compile-fixer SKILL.md/errors.md 保持纯方法论(具体错误已入种子,不写进 skill 静态内容)。
- agents/kingdee_plugin_agent/CLAUDE.md:常用操作「接真实金蝶环境」补编译服务 Windows 部署环境变量(COMPILE_SERVICE_REQUIRES_DLLS=1 / REFS_DIR / TARGET_FRAMEWORK=v4.8 / MSBUILD_PATH 可选)+ E2E 门达成 + DLL 来源 WebSite\bin(授权注意)。

---

## v1.12.0 — 2026-08-09(kingdee-plugin-agent:下发模板补验收标准/上限字段 + 设计文档 14→8 worker 偏差同步)

### 新增功能

- **下发模板验收标准字段(设计 §5.1)**:`Subtask.acceptance_criteria` 新字段 —— w1 拆解时 LLM(`PlanItem` schema 新增可选槽)按确认规格填写,未给 → 确定性兜底「按需求确认摘要验收」(LLM 路径与 llm=None 兜底路径一致);**w4 审查对照验收标准**:非空时注入 LLM context(`acceptance_criteria` 键 + human 提示「需求符合性是最高优先级审查项,未满足项按 severity 列入 findings,缺需求行为视为 Critical」),审查不止看规范库;w4_review.md 同步补对照说明;确定性审查路径(占位符检测)不受影响。
- **下发模板上限字段(设计 §5.1)**:`Subtask.max_rework`(0 = 全局默认 `GLOBAL_REWORK_BUDGET`)+ `Subtask.rework_count` —— 返工事件时 `agent.py::_advance_status` 先 rework_count+1,超过 max_rework(>0)→ 该子任务 failed 而非 needs_rework(w4 重审/w5 编译超限/w5_5 冒烟失败三条路径统一);与全局预算的协同:子任务上限是环节级更早触发的闸门,返工轮次已实际发生仍照扣全局预算(≤3 轮是任务级最终防线),两者叠加不抵消。
- **契约传递**:`_send_payload` 的 todo 全量快照自动携带新字段(`_as_state` 按 `_SUBTASK_FIELDS` 重建,旧 checkpointer 状态缺字段走默认值)。

### 测试

- 新增 9 项(全套 212 项):Subtask 新字段默认值 1、w1 LLM 拆解验收字段透传 + 兜底 2、w4 审查 context 含验收标准 + 空标准不误导 2、图级 max_rework 超限 → 子任务 failed(预算照扣)1、**三类型全覆盖** w2/w3/w4 确定性路径执行(bill/service/list 参数化 3 项,断言类型要点进设计骨架/类型基类进代码/占位符全渲染/审查 Approved);既有全局返工预算测试原样通过(默认 max_rework=0 走全局闸门)。

### 文档

- **设计文档 §3 偏差同步(实现偏差记录)**:14 worker(按类型拆 w2a/b/c、w3a/b/c、w4a/b/c)→ 实现为 8 worker + `TYPE_PROMPTS` 类型配置表(单源 `skills/<skill>/references/<type>.md`,等价 14 项职责全覆盖,类型知识 LLM 路径与骨架路径都完整传递);§2 编排行、§3.2 统一骨架、§4 数据流同步;§5.1 下发模板标注实现状态(验收标准/上限两项 ✅ 已落地,实际机制 = Subtask 字段 + `_send_payload` 快照)。
- tech.md:§2.1 Subtask 表补 3 字段 + 下发模板落地机制说明;§10.2 并发与轮次上限补子任务退回上限行。
- agents/kingdee_plugin_agent/CLAUDE.md:任务契约字段列表补验收标准/上限/rework_count;改任务契约操作说明(TaskState 级字段才需显式加 `_send_payload`);约束补子任务级上限与全局预算协同。

## v1.11.0 — 2026-08-09(kingdee-plugin-agent:死代码清理 + 冒烟链路 form_id/DLL 传递 + 反馈端点 + --env 记录)

### 新增功能

- **冒烟链路结构级修复(1/2)form_id 恒空**:w1 确认拆解时提取目标单据 FormId 写入 `state.environment["form_id"]`(w5.5 部署验证据此映射)—— `PlanOutput.form_id` 显式槽(LLM 拆解输出归纳)+ `RequirementWorker.extract_form_id` 兜底(从 decisions 中"单据/FormId"问题答案取首个标识符 token,llm=None 确定性路径同样可用);`_confirm_and_split` 只增不改 environment(保留 env_name 等键)。
- **冒烟链路结构级修复(2/2)DLL 传递**:`Subtask.dll_path` 新字段;编译链全通 —— `CompileResult.dll_path`(models)+ 真实 msbuild 后端编译成功后把输出 DLL 复制到服务端留存目录(`artifact_dir/<project_name>/Plugin.dll`,临时目录编译完即删)+ `/compile` 响应带 `dll_path` + 新增 `GET /dll/{project_name}`(project_name 过白名单防路径穿越)+ 客户端 `CompileClient._fetch_dll` 拉到本地(拉取失败优雅降级为空);w5 成功时存 `subtask.dll_path`;w5.5 冒烟验证对象改为 **DLL**(不再误用源码 Plugin.cs),无 DLL(如 mock 后端)→ `DONE_WITH_CONCERNS` 显式标注「无 DLL(编译后端未产出),跳过部署验证」,不扣预算不计冒烟指标;w6 打包 `dll_path` 非空时入包 `bin/Plugin.dll`。
- **反馈端点(设计 §12)**:`POST /tasks/{id}/feedback {reason}` 部署后行为错误手动上报 → 经验库 `propose("DEPLOY", sha256(reason)[:12], reason, …)`(proposed 态,与验收拒绝同沉淀模式:不同原因各自累计、相同原因去重);apikey 鉴权、404 未知任务、沉淀失败不阻塞反馈(never blocks),SSE 发 `feedback` 事件。
- **`--env` 消费(最小化)**:CLI/API 初始 state 新增 `environment: {"env_name": …}`(此前 environment 恒空 dict,节点不可见);只记录不做多环境切换(v1 单环境)。

### 死代码清理(评审确认无引用)

- 删 `Supervisor._check_budget`(仅 docstring 提及,预算判定在 `_decide` 第 4 步内联,等价逻辑同处)。
- 删 `RequirementWorker.interrupt_message`(无调用;agent.py w1 节点内联 payload dict 才是 interrupt 契约 —— API/CLI 按 `type` 字段分支)。
- `NEEDS_CONTEXT` 状态:设计契约保留值,当前无产出路径、无代码分支 → base.py `_report` docstring 注明"先接通 supervisor 处理再加,勿裸用"。
- `blocked` 状态:仍无写者,防御保留(守卫未来写者);STATUS_TO_WORKER 注释标注 defensive-only,`_ready_batch` 对 blocked 排除派发防忙循环。
- 删死 prompt 文件 `prompts/w5_5_smoke.md` + `prompts/w6_package.md`(SmokeWorker/PackageWorker 无 LLM 不加载 prompt,无代码/文档引用)。
- `state.py` 模块 docstring 的 `ask_question` 字段不存在 → 修正为实际字段(action/dispatch_id/user_feedback/metrics + started_at/spec_version)。

### 测试

- 新增 16 项(全套 201 项):form_id 提取 3 项(显式槽/decisions 兜底/LLM 拆解回写)、DLL 链 4 项(w5 存 dll_path、无 DLL 跳过不扣预算不计指标、dll_path 传至冒烟、w6 入包 bin/ 且无 DLL 无 bin/ 条目)、反馈端点 2 项(404/401、真实 ExperienceStore 两个不同原因累计 + 相同去重 + proposed 态)、--env 2 项(CLI 初始 state、API 初始 state)、编译服务 5 项(/compile 响应 dll_path、GET /dll 下载 + 404 + 路由层拦截 ../、project_name 白名单、客户端拉取到本地、拉取失败降级空);死代码对应测试改写 2 项(`test_budget_exhausted` 改测 `_decide` 预算判定、`test_w1_interrupt_message_…` 改测 record_answer + build_spec);冒烟指标语义更新 2 项(mock 后端无 DLL → 冒烟跳过,smoke_pass_count 不计数,接真实后端后恢复)。

### 修复(评审)

- **冒烟误用源码**(链路级):w5.5 原以 `Path(code_path)`(Plugin.cs 源码)调 deploy_and_verify,冒烟验证对象错误且 form_id 恒空;现 DLL 链路 + form_id 提取双修复(真实 DLL 仍待 P1 真实环境)。
- **/compile 写侧 project_name 路径穿越(Important)**:白名单此前只守 GET /dll 读侧;POST /compile 的 project_name 未校验即拼进 `artifact_dir/<project_name>/` 并 mkdir —— `../../references` 可任意目录写文件;入口套同一白名单,非法 → 400(后端不执行不落盘)。
- **form_id 提取误取 "FormId" 词(Minor)**:答案如"单据 FormId 是 SAL_SaleOrder"会取到 "FormId";finditer 跳过大小写不敏感的 formid token 后再接受。
- **DLL 拉取 OSError 未捕获(Minor)**:`_fetch_dll` 只捕 httpx.HTTPError,磁盘满/权限(mkdir/write_bytes 抛 OSError)会打崩 w5;改为 `except (httpx.HTTPError, OSError)` 降级为空 dll_path。

### 文档

- tech.md:w5.5 冒烟行(验证对象 DLL + 无 DLL 跳过)、w6 产物行(DLL 入包)、environment 字段说明(env_name + form_id)、§8.2 编译服务接口(dll_path + GET /dll)、§11 债务 `--env` 措辞(部分消费)+ 反馈通道说明。
- manual.md:§4 端点表补 `/feedback` 行、FAQ Q7 交付包内容更新、§6 zip 树更新(bin/Plugin.dll 条件入包 + records 已接线)、§7 单环境措辞更新。
- agents/kingdee_plugin_agent/CLAUDE.md:Subtask 契约补 dll_path + 冒烟链路说明;债务 `--env 未消费` → `部分消费`。
- 注:tech.md 无 §12 小节,反馈端点契约记于 api.py 模块 docstring + manual.md §4 端点表(设计 §12 对应)。

---

## v1.10.0 — 2026-08-09(kingdee-plugin-agent:P2 五项 —— 指标/失败收尾包/JSON 重试/records 接线/.env)

### 新增功能

- **任务指标随 State 统计(设计 §9/§12)**:`TaskState.metrics`(Annotated dict + `_merge_metrics` reducer 求和合并):compile_pass_count / compile_fail_count(w5 每次编译结果)、smoke_pass_count / smoke_fail_count(w5_5 冒烟结果)、rework_rounds(主管按返工事件累计,与预算扣减同源,w4 重审 + w5 超限 + w5_5 冒烟失败全覆盖);分支 worker 只上报**增量**(执行前后差值,并行分支 + 跨多轮派发不重复累计)。实测修复两处 LangGraph 通道陷阱:`_send_payload` 的 metrics 快照必须 `dict()` 拷贝(并行分支共享同一 dict 引用时 worker `+=` 原地改通道当前值 → reducer 重复累计,实测双任务 compile_pass_count=4 而非 2);Annotated 通道初始化不给 dataclass 默认值(初始空 dict)→ `_as_state` 缺键补齐 0 + reducer setdefault 补齐,计数键始终完整。
- **OTel span(设计 §12,复用 common/otel.py)**:worker 状态迁移(`kingdee.worker.<name>`,subtask_id/plugin_type/status 低基数属性)、编译轮次(`kingdee.w5.compile_round`,round/success)、主管决策(`kingdee.supervisor.decide`,action)各打 span;无 collector(no-op tracer)环境不崩;`api.py create_app` 启动时 `init_otel()`(与 sentiment api.py 同款,OTEL_ENDPOINT 配了才上报)。
- **失败收尾"未完成"包(设计 §8)**:终态 fail → 图路由到新 `w6_fail` 失败打包节点(先交包再 END):收集每个未交付子任务已有产物(design.md / Plugin.cs / review.json,缺失容忍)+ compile_errors(编译超限 5 轮后的错误日志,已记在 subtask)+ 审查裁决,`PackageBuilder.build_failed` 产出 `deliverable-failed-<ts>.zip`(文件名标注失败态),records/status.json 记原因(fail:返工预算耗尽/时间预算耗尽等)+ spec_version + 冻结 spec 快照,逐子任务目录 `subtasks/<sid>/` 存产物;CLI/API 与正常交付包同一通道展示 —— 原实现 fail 只有 TodoList 摘要。
- **LLM 畸形 JSON 重试(设计 §8)**:`structured_with_skill` 解析失败(parsed=None 且无 tool_calls)→ 同一份输入重试 1 次(共 2 次尝试),仍失败返回 None → worker 确定性骨架降级;重试与工具 2 回合上限正交(工具回合后的结果直接返回,不参与重试)。
- **交付包 records 接线(设计 §5.4/§12)**:w6 从产物库读 design.md + review.json 传入 deliverable 的 design/review 键(缺失容错)→ 随包落 records/design.json + records/review.json —— 原实现 records 恒为空 {},Minor 意见现在自动进包。

### 测试

- tests/test_kingdee_agent.py 新增 12 项:指标(w5 通过/超限计数、w5_5 通过/失败计数、默认全 0、图全链路含返工 rework_rounds=1、并行双任务合并不重复累计)、OTel span(fake tracer 记录 worker/编译轮次/主管决策 span 名与低基数属性,无 collector 不崩)、失败收尾包(返工预算耗尽 → zip 含原因 + compile_errors + 部分产物 + Minor 意见;时间预算耗尽 → 同样出包)、records 接线(design/review 真实内容进包;缺失容忍)、JSON 重试(失败 1 次重试成功返回;失败 2 次返回 None)。全套 184 项全过(172 既有 + 12 新增)。
- **评审修复(185 项全过)**:新增 zip 条目 id 净化测试 1 项(非法 id 替换为 "_"、空 id 兜底 unknown,无 "../" 穿越条目);span action 低基数断言并入既有 otel 测试(ask_user 带问题文本 → span 属性只记动作类型,所有 span 属性不含问题原文,遵循 OBS-CORE-003)。

### 修复(评审)

- **span action 高基数违规(OBS-CORE-003,Important)**:`kingdee.supervisor.decide` span 原记录完整 action,`ask_user:<问题>` 的问题文本是用户输入/LLM 生成的高基数自由文本;改为 `action.split(":", 1)[0]` 只记动作类型。其余新 span 已核验无用户派生文本(worker 的 subtask_id 过 ArtifactStore 白名单、plugin_type/status 枚举;编译轮次 round/success 数值)。
- **失败包 zip 条目 id 未净化(Minor)**:`build_failed` 的 `subtasks/<sid>/` 直接用 sid,脏数据可致 zip 路径穿越;非法字符替换为 "_"、空 id 兜底 "unknown"(复用 ArtifactStore 白名单模式,产物保留不丢弃)。
- **注入 builder 契约静默扩展(Minor)**:PackageBuilder 类 docstring 显式注明注入契约 —— 注入实例必须同时实现 build 与 build_failed(缺失时 AttributeError 显式暴露,不做静默降级)。

### 文档

- `.env.example` 新增 kingdee 配置组:KD_* 4 项(金蝶环境硬门槛)/ COMPILE_SERVICE_URL / COMPILE_SERVICE_REQUIRES_DLLS + REFS_DIR(注释态)/ KINGDEE_API_KEY,OTEL_ENDPOINT 归入 OpenTelemetry 分组 —— 与 manual.md §1.1 声明的 4 组配置一致。

---

## v1.9.0 — 2026-08-08(kingdee-plugin-agent:时间预算 + 需求版本冻结,设计 §8 两项落地)

### 新增功能

- **全流程时间预算(30min 图级总闸)**:`graph/state.py` 新增 `PIPELINE_TIME_BUDGET=1800.0` 常量 + `TaskState.started_at`(缺省 0.0 = 未设置,旧状态兼容不判定);CLI/API 建任务时把 `started_at=time.time()` 写入初始 state(**存于 state 而非 thread_id,interrupt 挂起 resume 后 checkpointer 恢复同一份值不重置**);`Supervisor.decide` 新增第 5 步确定性检查:超限且有未交付工作 → 剩余子任务标记 failed → `fail:时间预算耗尽`(与返工预算同语义);LLM 决策上下文摘要表新增"时间预算: 已用 Xs / 总闸 1800s"行,LLM 可自行选择 fail。单轮编译 ≤120s 与单任务编译阶段 ≤15min(5 轮 × 120s 天然 ≤10min)已由 CompileClient timeout 覆盖,本项只补图级总闸。
- **需求版本冻结**:`TaskState.spec_version`(缺省 1);spec 确认(`_confirm_and_split`)时显式盖章 `spec_version=1`,此后 requirement_spec 无任何写路径(w1 只在未确认时构建/修改 spec;确认后 ask_user 回答只记 user_feedback);API `deliver_answer` 加冻结锁 —— 确认后 answers 仅接受 ask_user 类型 interrupt 的恢复,其余 409「需求已确认并冻结,不能修改需求」(防未来回归松动冻结);w6 打包把 `spec_version` + 冻结 spec 快照写入交付包 `records/spec.json`(交付物可审计对应哪版需求),交付包契约 5 条目 → 6 条目。

### 测试

- tests/test_kingdee_agent.py 新增 8 项:TaskState 默认值(started_at=0.0 / spec_version=1)、decide 超预算(started_at 距今 2000s → fail:时间预算耗尽 + 剩余标记 failed)、started_at=0 正常派发不触发判定、LLM 上下文摘要含时间预算行、全流程确认后 `spec_version==1` 盖章、确认后中途 ask_user 回答不改 requirement_spec(快照前后一致)、API 建任务即写 started_at、API 确认后非 ask_user 恢复 409 / ask_user 恢复放行;test_kingdee_api.py 交付包契约更新为 6 条目并断言 `records/spec.json` 内容。全套 172 项全过(164 既有 + 8 新增)。

### 文档

- `agents/kingdee_plugin_agent/CLAUDE.md` 约束新增:时间预算(三级预算 + 总闸语义 + started_at 生命周期)与需求版本冻结(确认即冻结 + answers 锁 + 开新任务)两条。
- `docs/kingdee-plugin-agent/tech.md`:decide 顺序补第 5 步时间预算;TaskState 表补 `started_at`/`spec_version` 行;错误处理表 #22 时间预算更新为三级预算 + 图级总闸,#23 新增需求版本冻结(原 23-25 顺延为 24-26);§10.2 预算表补单任务编译阶段与全流程时间预算两行。
- `docs/kingdee-plugin-agent/manual.md`:answers 端点表补冻结 409 语义;常见问题新增 Q8(确认后改需求 → 开新任务)/ Q9(任务超时 → 30min 总闸);交付物解读补 `records/spec.json`。

---

## v1.8.1 — 2026-08-08(kingdee-plugin-agent 三份文档:项目/技术/使用手册)

### 文档

- **`docs/kingdee-plugin-agent/project.md`(项目文档)**:背景与痛点(重复劳动/API 复杂/编译门槛)、目标(需求 → 可部署交付包)、成功标准(编译通过率高/符合需求/返工少)、范围(单据/服务/列表 + 澄清→设计→生成→审查→编译→冒烟→打包→沉淀)、架构概览(1 主管 + 8 worker 循环图)、里程碑状态(三个 plan 全交付,164 项测试;待办:团队金蝶 DLL/E2E 门/真实环境联调/线上 DeepSeek 验证)、后续规划(知识自生长/多环境/多子任务单 zip/任务持久化与限流/其他 ERP 扩展方向)、技术栈。
- **`docs/kingdee-plugin-agent/tech.md`(技术文档)**:LangGraph 图结构(节点/边/interrupt/send/终态 + 完整 ASCII 图)、任务契约(Subtask/TaskState 字段、生命周期状态机、上报契约、审查裁决)、8 worker 详解(职责/输入输出/LLM 调用/降级)、skill 体系(6 个结构 + 渐进式披露 + load_skill 机制 + prompt 变薄单源原则)、知识库(四库设计/混合检索/经验库两态 + 签名去重/种子/检索路由表)、错误处理 25 场景表、安全(apikey/环境凭证/CORS/路径白名单)、部署(docker-compose 拓扑/compile_service/数据目录 gitignore)、测试(eval 集/注入约定/E2E 门)、性能与预算(recursion_limit 公式/并发上限/token 控制)、已知债务与未验证项。
- **`docs/kingdee-plugin-agent/manual.md`(使用手册)**:快速开始(.env 的 KD_* 4 项 + COMPILE_SERVICE_URL + KINGDEE_API_KEY + 种子灌入命令)、CLI 用法(命令/澄清交互/输出解读/退出码)、Web 用法(API + 演示页:澄清流/任务矩阵/验收操作)、API 端点表(5 端点)、常见问题 7 条、交付物解读(zip 内容)、限制与未验证项。
- 三份文档均基于代码实读编写,与 `agents/kingdee_plugin_agent/CLAUDE.md` 约束数值一致(返工预算 3/并发 3/编译轮次 5/澄清 10 轮/recursion 100+20n),未验证项(线上 DeepSeek/真实金蝶环境/E2E 门)显式标注。交付报告:`docs/superpowers/plans/kingdee-docs-report.md`。
- **勘误(评审修复)**:容器启动语义(无 DLL 时 docker-compose 启动即失败,非"mock 兜底")/ 交付物内容(DLL 恒不入包、records 为空占位)/ api_ref bm25_weight 接线声明(默认 0.5,0.7 为约定未接线)/ needs_rework 恒映射 w3 / 演示页 PHASES 以"交付"结尾 / skill_summary 仅注入 w1 等 9 处修正,详见交付报告"勘误"节。

---

## v1.8.0 — 2026-08-08(w2 设计阶段经验库回流:历史坑 → 设计规避)

### 新增功能

- **w2 设计接经验库(DesignWorker experience 参数,默认 None 与 rag 同模式)**:按子任务标题 `search_related(title, title, k=3)` 检索历史踩坑(设计阶段无编译错误码,标题同时充当 code/message 双信号),命中注入设计 LLM 上下文的"历史踩坑参考"段 —— 条目带 text + status/confidence,verified 优先排序(proposed 自核后采用),显式标注"仅供参考、非必须满足";检索故障 try/except 降级为空命中,不阻塞设计(与 RAG 检索同一纪律)。`build_graph` 把 experience 透传给 w2(原仅 w5/w7),docstring 同步。
- **knowledge-steward 路由表**:experience 行补 w2(设计时历史坑检索、title 语义、命中注入设计上下文、verified 优先)。
- **design-builder 方法论**:流程步骤插入"查历史踩坑(经验库)"(设计前先查历史坑,把已知错误模式转化为设计规避 —— 如签名级联 → 设计时核对基类事件签名),输入清单补经验库检索上下文。

### 测试

- tests/test_kingdee_agent.py 新增 2 项:w2 经验命中注入设计 LLM 上下文(标题双信号 + k=3 调用契约、human 消息含"历史踩坑参考"与命中文本、verified 排在 proposed 前、设计落盘)/ 经验库故障降级仍 DONE;既有 w2 测试(experience=None)不变;全套 164 项全过(162 既有 + 2 新增)

---

## v1.7.1 — 2026-08-08(评审修复:seed_load CLI 入口 + ExperienceStore.archive + 路由表勘误)

### 修复(评审)

- **seed_load 补 `__main__` 入口(Important)**:维护手册步骤 1.4 记录的 `python -m agents.kingdee_plugin_agent.seed.seed_load` 原为 no-op(无 `__main__` 块,import 后直接退出);新增 `main(argv)` + argparse(`--data-dir` 可选,默认 data/kingdee-rag 与 RagClient 一致),打印 "种子灌入完成:新增 N 条";maintenance.md 步骤 1.4 同步为真实调用
- **ExperienceStore 补 `archive()`(Minor)**:维护手册归档步骤原为手搓 chromadb 元数据更新;新增 `archive(signature)`(与 verify 同路径 `_set_status` 重构,status → archived,文档与向量不动),search_related 既有过滤逻辑天然排除 archived(仅返回 proposed/verified);maintenance.md 步骤 4 改用该 API
- **knowledge-steward SKILL.md 路由表勘误(Minor)**:api_ref 行删除 "w4 审查(API 抽查指引)"(ReviewWorker 不检索 api_ref),补脚注 "w4 的 API 抽查凭模型知识与模板基线比对完成,未接 api_ref 检索;接入属后续增强"

### 测试

- 新增 3 项:seed_load CLI `main(["--data-dir", tmp])` 冒烟(首跑 n>=7 + 打印契约 + 二次幂等 0)/ ExperienceStore archive 流程(proposed 与 verified 均可归档,归档后 search_related 排除,文档与元数据仍在库内)/ archive 未知签名抛 RagError;全套 162 项全过(159 既有 + 3 新增)

---

## v1.7.0 — 2026-08-08(knowledge-steward 知识库全生命周期方法论)

### 新增功能

- **knowledge-steward skill**(skills/ 下,与既有 5 个并列,共 6 个):
  - SKILL.md:沉淀方法论(什么值得沉淀/条目格式/签名去重/proposed→verified/不阻塞纪律)+ 维护手册摘要(种子增补/文档导入/规范库合并/定期 review)+ **检索路由速查表**(api_ref/guide/experience × w2-w5,bm25_weight 0.7 约定、search L2 低=好 vs hybrid RRF 高=好的分数方向警示)
  - references/distillation.md:沉淀质量标准(条目模板 + 好例/坏例对比、去重边界、签名规则速记表、verify 判据)
  - references/maintenance.md:维护操作手册(种子增补/文档导入/规范库合并/经验库 review 四类操作分步,幂等可重跑)
- **loader.py 注册第 6 个 skill**:`_AVAILABLE_SKILLS["knowledge-steward"]` 摘要(沉淀方法论 + 维护手册 + 检索路由);SKILL_HINT 按阶段提示追加 知识沉淀(knowledge-steward)(w1-w5 LLM 也可按需调用)
- **w7 绑定决策(不绑定)**:w7_distill 为确定性代码(无 LLM 调用,compile_errors 全量 propose),不绑定 load_skill;SKILL.md 显式注明沉淀决策由代码规则完成,"什么值得沉淀"标准作为人工 review / 未来 LLM 化的参照(避免给无 LLM 节点挂无用工具)

### 测试

- tests/test_kingdee_agent.py 新增 1 项:load_skill('knowledge-steward') 内容断言(SKILL.md 含 api_ref/bm25_weight 0.7/L2 vs RRF 分数方向/proposed→verified/无 LLM 绑定说明;references = distillation.md + maintenance.md,含条目模板好例坏例/维护四件套);test_skill_summary 更新为 6 项断言 + knowledge-steward 摘要关键词;skill 全可加载测试扩到 6 个

---

## v1.6.1 — 2026-08-08(errors.md 纯方法论化:错误条目单一来源经验库(动态))

### 重构

- **compile-fixer/references/errors.md 改为纯方法论**:移除全部静态 错误码 → 根因 → 修法 映射(旧分类表 CS0246/CS0103/CS0234/CS1061/CS0506/CS0115/CS1002/CS1525/CS1519 + "经验条目(seed)" 标注),只保留 错误分类框架(分析维度+判断自问)/ 根因分析方法(级联找源头、签名错是级联源、阻断性优先)/ 经验库检索策略(按错误码+消息语义 search_related,verified 优先、自核后采用)/ 修复纪律(5 轮上限、禁止原样重提交、修复后必重编);显式声明 "具体错误映射见经验库(启动种子 seed/compile_errors.json + w7 沉淀),新踩坑不写这里,走 w7 沉淀"
- **SKILL.md / loader 摘要 / w5_compile.md 措辞同步**:load_skill('compile-fixer') 交付内容描述从"错误模式库"改为"方法论 + 检索指引";根因定位步骤不再列举具体错误码(具体根因链与修法从 compile_errors 的 experience 附注取)
- **w5 检索路径核实为纯动态、无需改动**:`_retrieve_fix` 按错误码+消息语义 `search_related`(k=2)把命中附注到 compile_errors 的 experience 字段,`_llm_fix` 将含 experience 附注的 compile_errors 序列化进修复 context —— 修复 LLM 看到的是 编译错误 + 经验库命中 + 方法论(load_skill),无静态错误表参与

### 测试

- 新增 2 项:errors.md 纯方法论契约(无 `CS\d{4}` 静态映射、"经验条目(seed)" 标记消失、单一来源指向经验库)/ 经验库命中真实进入修复 LLM context(捕获 fake LLM 每轮 human 消息断言附注注入);更新 1 项(codegen+review+fixer references 断言从错误码改为方法论词)

### 修复(评审)

- **B 类(签名不匹配)具体指引回归经验库**:评审发现 errors.md 删除后签名类失去唯一具体指引(旧文件含 OnLoad/AfterDoOperation 等正确签名),且种子无 CS0506/CS0115 条目 → 种子补 2 条(共 7 条):CS0506(不是重写,基类无此成员 → 核对基类签名 + 模板基准)、CS0115(找不到可重写方法 → 确认事件名与签名一致,签名错是级联源头);条目 message/fix 含类型模板指针(templates/<type>/template.cs 基准)
- **契约测试覆盖 SKILL.md**:errors.md 纯方法论契约扩展为 errors.md + SKILL.md 双文件扫描,均断言零 `CS\d{4}` 静态映射;seed_load 幂等测试断言更新为 n1 >= 7

---

## v1.6.0 — 2026-08-08(每 worker 方法论 skill:design/codegen/review/compile-fix + prompt 变薄)

### 新增功能

- **4 个方法论 skill**(skills/ 下,与 requirement-clarify 并列,共 5 个):
  - `design-builder`(w2):设计方法论(事件绑定决策/控件与字段映射/拦截方式/联动单据/异常骨架)+ references/{bill,service,list}.md 三套完整检查清单(每项含"必须给出结论"与检查自问)
  - `code-generator`(w3):生成方法论(模板优先/指南参数化/冲突以模板为准/占位符清零)+ references/{bill,service,list}.md 生成要点与自检清单
  - `code-reviewer`(w4):审查方法论(规范库整库对照/API 抽查/模板基线比对/裁决规则 Critical-Important-Minor)+ references/{bill,service,list}.md 审查重点
  - `compile-fixer`(w5):编译修复方法论(错误分类/经验库检索策略/修复优先级/5 轮重编纪律/防重复提交)+ references/errors.md 常见编译错误模式库(基于 seed compile_errors.json 5 条扩展:CS0246/CS0103/CS0234/CS1061/CS0506 分类表 + 修法)
- **loader.py 支持 references/ 子目录**:load_skill 的 references glob 兼容两种形态(老形态模板直放 skill 目录 + 新形态 references/ 子目录),name→content 映射交付不变
- **worker prompt 变薄,方法论单源化**:w2_design/w3_generate/w4_review/w5_compile 四个 base prompt 去掉方法论段落,保留 角色一句话 + 输入输出契约 + `load_skill('<skill>')` 提示;9 个类型分支文件(w2/w3/w4 × bill/service/list)删除,内容并入对应 skill references,worker TYPE_PROMPTS 改为指向 `skills/<skill>/references/<type>.md`(base._load_prompt 支持 "/" 路径解析)—— prompts 与 load_skill 从同一份文件取类型要点,不再双份维护

### 修复(评审)

- 新 w4_review.md 的 JSON 契约样例单花括号经 ChatPromptTemplate f-string 解析失败 → 回退确定性骨架(裁决失真),恢复 `{{...}}` 转义(dev-standards §7.2 陷阱重踩实录)

### 测试

- tests/test_kingdee_agent.py 新增 4 项(5 skill 全可加载 / design-builder references 内容断言 / codegen+review+fixer references 内容断言 / TYPE_PROMPTS 指向 skill references);test_skill_summary 更新为 5 项断言;全套 156 项全过(152 既有 + 4 新增)

---

## v1.5.0 — 2026-08-08(load_skill 机制:requirement-clarify 渐进式披露,对照 sentiment 模式)

### 新增功能

- **skill 渐进式披露(skills/loader.py)**:`load_skill(skill_name)` 工具(摘要启动加载,按需取 SKILL.md + 三套类型模板;requirement-clarify 无 references/ 子目录,模板直放 skill 目录)+ `skill_summary()` 摘要注入 + `SKILL_HINT` 提示常量;未知 skill → error JSON 并列出可用项
- **worker 绑定**:w1-w5 五个 LLM 调用点改经 `structured_with_skill` 绑定 load_skill —— 官方 `tools` 参数(json_schema + include_raw,经安装包 introspection 核实:bind_tools 后再 with_structured_output 会经 `__getattr__` 委派丢失 tools,必须用 tools= 形态),最多 2 回合(回合 1 调工具 → 喂回 ToolMessage → 回合 2 出 schema);脚本/fake LLM(无 bind_tools)自动跳过绑定,既有测试契约不变
- **w1 澄清 prompt 注入 skill 摘要**:摘要 JSON 走模板变量占位(dev-standards §7.2 f-string 花括号陷阱)
- **技能文档**:`skills/requirement-clarify/SKILL.md`(一次一问/多选优先/元数据驱动/10 轮上限/决策+假设记录,引用 bill/service/list 模板)

### 修复(评审)

- load_skill references 交付模板正文(name→content 映射,LLM 无文件工具,只给文件名等于没给)
- sentiment loader 目录名 dash→underscore(load_skill 恒"目录缺失"既有 bug)

### 测试

- tests/test_kingdee_agent.py 新增 6 项(load_skill 内容交付 + 未知 skill 报错 / skill_summary / 工具回合→schema 回合 / 单回合直返 / 2 回合上限 / 解析失败→None);全套 152 项全过

---

## v1.4.0 — 2026-08-08(kingdee-plugin-agent 全流程交付首版:CLI + Web + 文档)

### 新增功能

- **CLI 入口(cli.py)**:`run_cli` 需求文本 + `--env` 目标环境;环境硬门槛(未配 KD_BASE_URL → exit 1,不进图);stdin 交互澄清循环(interrupt 挂起 → 打印问题/确认摘要 → 答复恢复);结束打印 TodoList 摘要 + 交付包路径(全部交付返回 0,失败/中止返回 1)
- **Web API(api.py)**:`create_app` 工厂 + 5 接口 —— POST /tasks(apikey 鉴权 + KD_* 4 项硬门槛,缺任一 503)/ GET /tasks/{id}/events(SSE 实时流,断线重连按 seq 重放)/ GET /tasks/{id}/state(全量快照兜底)/ POST /tasks/{id}/answers(澄清恢复,`Command(resume=...)`)/ POST /tasks/{id}/acceptance(验收,拒绝原因喂 w7 经验库);每任务独立图 + MemorySaver + 后台线程
- **前端演示页**:`web/kingdee-demo.html`(SSE 任务矩阵 + 澄清流 + 验收)
- **agent 专属 CLAUDE.md**:`agents/kingdee_plugin_agent/CLAUDE.md`(按 dev-standards §6 模板:职责/架构/常用操作/约束)

### 修复

- api.py 补 CORS 中间件(演示页跨域访问;test_api_cors_preflight)
- acceptance→w7 沉淀签名 reason 感知(sha256 摘要入 file_pattern,不同拒绝原因不被去重吞掉;同原因仍去重)

### 测试

- tests/test_kingdee_agent.py 67 项全过(图全链路 + CLI/API 确定性注入路径),tests/test_kingdee_api.py 8 项全过;全套 143 项全过(含 tests/eval 生成质量 eval 4 项)

---

## v1.4.1 — 2026-08-08(Plan C 终审 fix wave:依赖声明/容错/预算/安全)

### 修复(终审 1 Critical + 3 Important + 2 Minor)

- **requirements.txt 补 sse-starlette**(Critical):api.py 依赖 `EventSourceResponse` 但未声明(仅 .venv 预装可用);按 .venv 实测版本 pin `sse-starlette>=3.4.8`,fresh-venv 安装模拟(fastapi+uvicorn+sse-starlette)验证通过。
- **CompileClient 超时 10s → 120s**(Important):单轮编译按设计 ≤2min,10s 会误杀真实编译。
- **w5 捕获 httpx.HTTPError**(Important):编译期间超时/连接失败(TimeoutException/ConnectError 均系 HTTPError 子类,实测类层级)→ BLOCKED「编译服务不可用(超时/连接失败)」,不计轮次不扣预算;原实现异常向上传播 → 节点 raise → API 任务死/CLI traceback。
- **recursion_limit 公式放宽**(Important):`default_recursion_limit` 50+10×n → 100+20×n(n=10 → 300;旧 150 < 实际需求 ~160,n=7 亦无返工余量 → GraphRecursionError)。
- **ArtifactStore 子任务 id 白名单**(Important):`^[A-Za-z0-9_-]+$` 校验,LLM 生成 `..`/`/` 携带 id → ArtifactStoreError,防越出 artifacts 根目录写文件。
- **supervisor LLM finish 门控**(Minor):finish 仅 `_all_delivered` 时放行;澄清期(todo 空)幻觉 finish → 回落确定性兜底(原实现零交付结束图,CLI 误报成功)。

### 测试

- tests/test_kingdee_agent.py 追加 3 项(编译 HTTP 错误 BLOCKED 不扣预算 / 产物 id 路径穿越拒绝 / 主管幻觉 finish 回落),全套 146 项全过。

---

## v1.3.0 — 2026-08-08(kingdee-plugin-agent 主管图构建 C10)

### 新增功能

- **kingdee-plugin-agent 主管图(agent.py)**:LangGraph 循环图接线 —— 主管决策节点(supervisor)+ 批量派发节点(dispatcher)+ 8 个 worker 节点(w1 需求澄清 / w2 设计 / w3 生成 / w4 审查 / w5 编译修复 / w5.5 冒烟 / w6 打包 / w7 沉淀)+ interrupt/send。
- **用户交互(interrupt)**:w1 澄清循环逐问挂起(问题清单 → 确认摘要,上限 10 轮),中途主管 ask_user 挂起;`Command(resume=answer)` 恢复。
- **并行派发(send)**:`Command(update, goto=[Send...])` fan-out,依赖满足的子任务批量 in_progress,并发 ≤3;todo 按 id reducer 合并。
- **终态处理**:全部 delivered → finish;返工预算耗尽/存在失败 → fail(剩余子任务标记 failed,依赖失败级联);LLM 结构化决策(run/ask_user/finish/fail)带动作校验,确定性路径兜底。
- **langgraph.json 注册** `kingdee_plugin_agent`(graphs 入口 build_graph)。
- **LLM 接线(worker 内真实调用点,可注入)**:w1 提问/拆解、w2 设计(RAG guide+api_ref)、w3 生成(模板+指南)、w4 审查(规范注入)、w5 编译修复改写(经验库附注)—— ChatPromptTemplate + with_structured_output,失败回退确定性骨架。

### 修复(C1-C9 终审 carry-over,随 C10 一并落地)

- 未知 plugin_type → worker ERROR 上报 → 子任务 failed(不再裸 KeyError)
- w4 审查产物改走 `review_path` 字段(移除 run() 覆写,基类契约原样)
- w5 编译修复循环:LLM 真实改写代码写回重编(非原样重提交);经验库检索 try/except 不阻断
- w5.5 冒烟:显式传 Path(契约对齐 SmokeClient);客户端未配置 → BLOCKED 不扣预算 → failed(防无限重试)
- w1 spec/plan 落盘 JSON(非 repr)
- w6 多子任务交付合并(v1 逐包):包文件名带子任务 id,全部记入 `final_deliverables`
- 并行分支同一步写通道问题:todo/deliverables 用 reducer;预算改由主管统一扣减(rework_events 上报)

### 测试

- tests/test_kingdee_agent.py 追加 20 项:图全链路可达性(fake LLM 脚本化)、interrupt/resume、并行派发(2 独立子任务同步 in_progress + 并发上限)、终态(finish/fail/失败依赖级联)、返工循环、中途 ask_user、各 carry-over 修复点单测;`pytest tests/test_kingdee_agent.py` 50 项全过,全套 122 项全过。

## v1.2.0 — 2026-08-07(轨 key 语义化 + 移除风险等级)

### 变更

- sentiment-query-agent:轨 key 语义化(a/b/c → 全量新闻/负面新闻/行业新闻),任务 ID 形如 Q0-全量新闻
- sentiment-query-agent:全链路移除风险等级(critical/high/medium/low):step6/state/nodes/converter/Excel/skill 文档/demo
- 保留:风险词(R 层词表、负面新闻轨 AND 条件)、频次定级、相关度 direct/indirect/context
- 兼容:LLM 多余 risk 输入被 step6 忽略;旧字母轨 key 校验失败记 GAP

---

## v1.1.0 — 2026-08-07(load_skill 方法论接入)

### 新增功能

- **load_skill 工具接入(方案 2a)**:每步节点绑定 load_skill 工具,LLM 需要方法论时主动调用(六层词表/双轨语法/信源/频次规则),拿到专业指导后按格式输出。最多 2 回合(回合 1 并行调工具 → 回合 2 生成 JSON),防死循环。
- **SKILL.md 补全**:工作流每步补「完成后调用脚本」指令(脚本调用约定/步骤对应/数据流/格式契约),skill 成为自包含知识包;标注方案 A(代码调用脚本)+ load_skill 方法论供给。

### 质量提升(load_skill 前后对比)

- 风险分级更有区分度:厄瓜多尔 c 轨 high、刚果金 b/c 轨 high(之前普遍 low/medium 偏保守)
- 快讯轨普遍正确配置(快讯/小时级)
- 方案名更具体:米拉多铜矿/迪兹瓦微电网/蒙古 ETT 选煤厂(之前泛"项目群")
- 识别地区更全:新增蒙古

### 技术要点

- DeepSeek JSON Mode + tool calling 兼容:实测 `bind_tools([...], strict=True)` + `response_format={"type":"json_object"}` 可同用,LLM 正确发 tool_calls 且后续输出纯 JSON
- 多轮工具调用:回合 1 tool_calls → 执行 load_skill → 喂 ToolMessage → 回合 2 生成 JSON

---

## v1.0.0 — 2026-08-07(sentiment-query-agent 正式交付)

首个完整交付版本:海外舆情检索方案生成 Agent。

### 新增功能

- **六步流水线**:输入中文公司名,自动完成实体测绘 → 主体画像 → 关键词字典 → 双轨检索式 → 属地信源 → 频次定级,每步产物实时可见
- **方案组生成**:输出方案组 + 组内多方案 × 多轨(a 全量 / b 精准 / c 不点名 / 快讯 / 司法 / 招标),含频次/风险等级/GAP 数据缺口标注
- **API 服务**(7 接口):提交任务 / 查进度 / 获取方案组 / 提交勾选 / 确认入库 / 导出 Excel / 健康检查
- **勾选确认机制**:方案级 + 轨级两级勾选,确认入库后冻结,可导出三 sheet Excel(检索任务清单 / 关键词字典 / 调度说明)
- **鉴权与计费**:apikey 鉴权 + 资源归属校验(越权 403)+ 每次完整生成计费 1 单位(并发安全)
- **前端演示页**:`web/demo.html` 六步实时回显 + 勾选入库导出全流程
- **领导汇报技术说明书**:`web/tech-doc.html`(为什么开发 Agent / 技术实现 / 演示方式)

### 技术要点

- LangGraph 状态机编排 6 步流水线,AsyncSqliteSaver 持久化(中断续跑)
- DeepSeek JSON Mode 强制结构化输出 + skill 分步脚本格式契约校验(缺字段自动记 GAP)
- gateway MCP websearch 池(brave / tavily / serpapi 三引擎,失败自动切换)
- OpenTelemetry 全链路可观测(OTLP exporter)
- 内网可访问(服务绑定 0.0.0.0)

### 修复

- 流水线端到端跑通系列:LLM 输出非 JSON / 模板花括号转义 / 同步调用阻塞事件循环 / 路径层级错误 / step4 轨 key 误判 / step6 索引越界
- 计费并发竞态:线程锁 + fcntl 文件锁,并发提交不丢记录

### 文档

- `docs/api.md` 接口文档(7 接口 + 错误码)
- `docs/deployment.md` 部署文档(配置/启动/运维/常见问题)
- `docs/dev-standards.md` 开发规范(§7 通用开发经验 15+ 条踩坑记录)

### 变更

- agent1 更名为 sentiment-query-agent(业务名,展示层连字符;包名 sentiment_query_agent 下划线)

---

## v0.2.0 — 2026-08-06(agent1 重构为舆情方案生成 Agent)

### 新增

- agent1 从通用骨架重构为海外舆情检索方案生成 Agent(设计文档评审 2 轮)
- skill 分步脚本模式:6 个 stepN.py 作为格式契约执行器(校验/标准化/记 GAP)
- skill 原生加载(渐进式披露 load_skill)+ 项目内 skill 目录策略(agent 专属 / common 共享)
- 6 步输出格式契约(§5.1,字段对齐 skill 最终 spec,导出零转换)
- auth/billing 拆分:apikey 鉴权 + 归属校验,计费 pending → committed(防刷限并发)

### 修复

- 设计审核修正:资源归属校验 / 计费冻结语义 / 导出转换层 / skill 分步加载 / OTel 高基数约束 / MCP 连接生命周期

---

## v0.1.0 — 2026-08-06(项目初始化)

### 新增

- 项目骨架:common 共享层(LLM 工厂 / 配置 / prompt 加载)+ agents/agent1 通用骨架(占位文档)
- 多供应商模型工厂:供应商注册表,换供应商改 `.env` 不改代码
- CLAUDE.md 项目指南 + 开发规范(必须依据 langchain MCP 文档/API 开发)
- 每个 agent 独立 CLAUDE.md 约定(§6 模板)

### 文档

- 设计文档:agent1 目录架构设计(LangGraph)
- 开发规范初版:开发铁律 / 架构约定 / 开发流程 / CLAUDE.md 模板
