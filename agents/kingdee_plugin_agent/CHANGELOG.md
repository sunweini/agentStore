# kingdee-plugin-agent 版本更新说明(CHANGELOG)

> 版本号独立管理(每 agent 独立序列),历史从根 CHANGELOG 迁移(2026-08-12)。
> 收尾规则:改动归本 agent → 更新本文件 + bump 版本号(当前最大号 +1)。

---

## v1.26.0 — 2026-08-10(终审修复 —— 恢复死锁 + 重复恢复 + 文档一致性)

### 修复

- **恢复死锁(C-1,终审 Critical)**:`_restore_pending` 原在主线程对每个恢复任务
  「先启线程再阻塞 acquire」—— 恢复线程挂在 interrupt 等用户回答不释放配额,
  挂起任务数 > `KINGDEE_MAX_CONCURRENT`(默认 4)时第 N+1 个任务永久阻塞,
  create_app 起不来(实证:服务挂了 → 用户睡觉 → 早上 5 个挂起任务 → 起不来)。
  改为非阻塞 acquire(blocking=False),配额不足跳过该任务(元数据回写 created,
  留待下次重启恢复)—— 恢复语义是「不漏任务」而非「限制恢复」。补测试
  `test_restore_skip_when_capacity_full`(5 个 created 任务 + 容量 4 → create_app
  秒回、容量内恢复、超限跳过仍 pending)。
- **恢复任务被重复恢复(C-2,终审 Critical)**:① 恢复前占位幂等 ——
  `UPDATE tasks SET status='running' WHERE id=? AND status='created'`,影响行数
  0 = 已被别实例认领,跳过(防同一 DB 双实例并发扫描同 thread_id 双线程 invoke,
  checkpoint 竞态/双倍计费);② cancel 路径也落终态(`cancelled`,原直接 return
  不落盘 → DB 永远 created 每次重启重放)。补测试
  `test_restore_claim_idempotent_skip_running`(二次认领返回 False + 不在待恢复
  列表)、`test_restore_claim_unknown_task_returns_false`、
  `test_cancel_persists_terminal_status`(cancel → 'cancelled' 落盘)。
- **加载期 int() 容错(I-5)**:`KINGDEE_MAX_CONCURRENT` 非数字配置 import 即崩
  (500 全 API),改解析失败回落默认 4 + warning 结构化日志;`.env.example` 补
  `KINGDEE_MAX_CONCURRENT` / `KINGDEE_TASKS_DB` 注释(运维可见性)。
- **文档一致性(I-1~I-4)**:CLAUDE.md 债务清单改「已清偿(v1.21.0)+ 剩余项」
  两段式;manual.md §7「未线上验证」整段更新(load_skill/WebAPI 均已实测);
  tech.md §9 故障表 L25 改持久化语义 + §7 安全段 apikey 债务移除 + §11 债务
  列表同步;project.md 待办勾销(WebAPI 联调 / load_skill 验证 / 任务持久化);
  agent.py `checkpointer` 注释 AsyncSqliteSaver → 同步 SqliteSaver;loader.py
  docstring 旧节名修正;api.py 注释「KD_* 5 项」→ 4 项。
- 测试:新增 5 个回归测试(上述),终审修复后全量 kingdee 测试全绿。

---
## v1.25.0 — 2026-08-10(任务持久化 —— SQLite checkpointer + 重启恢复)

### 行为变更

- **API 任务不再内存存储,重启不丢**(清偿 v1 债务「内存任务存储」):create_app
  统一用同步 `SqliteSaver`(共享连接,`check_same_thread=False` + 内部锁,官方
  docstring 确认线程安全)替换每任务 MemorySaver —— 同步版贴合后台线程
  `graph.invoke` 架构(AsyncSqliteSaver 需 ainvoke/asyncio.run 包装,不用);
  checkpointer 存 `data/kingdee-tasks.db`(KINGDEE_TASKS_DB 可覆盖,data/ 已
  gitignore)。
- **启动恢复**:`create_app` 启动扫描 tasks 元数据表(id/env/status/created_at/
  requirement)status='created' 的任务,按 env 重建图 + 后台线程续跑;
  checkpoint 已落盘的任务用 `get_state` 读回 checkpoint 原 state 作输入,
  **fresh-run 重放挂点**(挂起处 interrupt 原样返回,挂起等用户回答;started_at
  保留原值,时间预算不重置,设计 §8「挂起 resume 不重置」—— 用新 time.time()
  会从重启时刻重新计时,违反冻结语义);checkpoint 缺失(建任务后线程未跑即
  崩溃)的任务才用元数据表构造初始 state 从头跑;任务结束/失败落盘终态,
  重启不再恢复。
- **恢复输入排除 metrics 键**(re-review Critical):metrics 是求和 reducer
  (`_merge_metrics`),恢复输入带 checkpoint 当前值会被 `operator(current, v)`
  再算一次 —— compile_pass_count/compile_fail_count/smoke_pass_count/
  smoke_fail_count/rework_rounds 五计数器恢复后翻倍(多次重启逐次累计)。
  去掉 metrics 键 = 该通道不产生输入更新,保留 checkpoint 原值;其余 reducer
  通道(todo 按 id 合并 / rework_events 替换 / final_deliverables 去重追加)
  对同值输入幂等,无此问题。
- **恢复任务配额语义**:恢复任务 `_sem.acquire()` 阻塞等待(重启场景不 429
  拒绝),`_run_loop` finally 统一 release 配对;元数据写入用短生命周期连接 +
  INSERT OR IGNORE(恢复幂等,首建记录为准)。
- **msgpack 序列化兼容**(清偿 v1 债务「msgpack 反序列化警告」):TaskState/
  Subtask dataclass 经 JsonPlusSerializer 显式白名单
  (`allowed_msgpack_modules`),消除 unregistered-type 反序列化警告 —— 默认
  宽松模式未来版本会收紧,提前显式登记。
- 生产路径 `build_graph(env=..., checkpointer=app.state.saver)` 显式注入共享
  checkpointer(MemorySaver 默认换掉,重启后 checkpoint 会话可见)。

### 测试

- 新增 `test_restore_pending_task`:建任务 → 等 interrupt 挂起 → 同 DB 新 app
  恢复(env 透传断言)→ 答澄清(等 confirm 挂起再投递,防恢复后 409)→ done →
  终态落盘 → 复位 created → 再次恢复(终态 checkpoint 重放自动 done)。
- 新增 `test_restore_recovers_task_hung_at_interrupt`:恢复语义三件套断言
  (注入共享 SqliteSaver 图,与 create_app 共享 checkpointer 同实例):恢复后
  挂 confirm 挂点(重跑则回 question round 0)/ `clarify_answers` 保留已答
  答案(重跑则空)/ `started_at` 保留原值(重跑则被新时间戳覆盖)。
- 新增 `test_restore_metrics_nonzero_not_doubled`:metrics 非零时重启恢复不
  翻倍(w1 挂起会话 `update_state` 注入 metrics=1 → 重启 → 断言仍 =1,非 2/3;
  无修复时 FAIL)。
- `test_api_production_build_graph_receives_env` 增补 checkpointer 透传断言
  (生产路径必须注入共享 saver,否则持久化失效)。
- conftest:新增 autouse `_reset_api_concurrency_sem` —— 模块级 Semaphore 跨
  测试残留,前面测试的挂起任务线程(30s 超时)占满配额后,后续恢复任务的
  `_sem.acquire()` 主线程阻塞 → 全量套件死锁(单跑不复现);每测试重置满配额。
- 全量 164 测试全绿(agent 146 + api 18;审查修复后复跑 164 passed 95.80s)。

---

### 文档

- **code-generator 强化「禁止编造 API(签名必须有来源)」**(skill-creator 评估
  发现:无 skill 时 LLM 编造 InvServiceHelper.QueryInvQty / InvQueryParam /
  InvQueryResult.AvailableQty 等看似真实的 API,编译必挂):
  - 新增坏例/好例对比:坏例 = 编造的 API 调用(带看似真实的参数/返回类型
    注释);好例 = 显式 TODO 骨架 + return 默认值 + 注释"签名未在元数据/
    guide 确认,禁止编造";
  - 明确标准:库存查询/服务调用等外部 API,签名必须有来源(guide 检索命中/
    元数据确认/模板);无来源一律 TODO 占位,禁止凭记忆补全;
  - 说明为什么:编造 API 编译必挂,烧掉整条编译-修复循环(吃 w5 轮次与返工
    预算);TODO 占位编译通过,由后续元数据接线补全;
  - references/bill.md 补「服务调用不编造」要点与自检项;loader 摘要同步
    强化(无来源外部 API 一律 TODO 占位,禁止编造)。
- **knowledge-steward 强化 verify 建议必填**(评估发现:无 skill 蒸馏无
  proposed/verified 两态纪律):
  - 沉淀条目格式增加验证字段:proposed 态必填 —— 复现方式或人工确认人;
  - 明确无 verify 路径的沉淀不要写(一次性/无法复现的观察不沉淀);
  - 解释为什么:proposed 无 verify 路径 = 污染风险(幻觉修复被当知识),
    验证建议是防污染的收口(propose 时填的验证字段 = 后续 review 的作业清单);
  - references/distillation.md 条目模板/好例坏例/判据同步;loader 摘要补
    "proposed 必带验证建议"。
- 全量测试:272 全绿(契约断言短语未变,仅内容增强)。

---
## v1.20.0 — 2026-08-10(skill 评估改进 —— 禁编造纪律强化 + verify 建议必填)

### 文档

- **code-generator 强化「禁止编造 API(签名必须有来源)」**(skill-creator 评估
  发现:无 skill 时 LLM 编造 InvServiceHelper.QueryInvQty / InvQueryParam /
  InvQueryResult.AvailableQty 等看似真实的 API,编译必挂):
  - 新增坏例/好例对比:坏例 = 编造的 API 调用(带看似真实的参数/返回类型
    注释);好例 = 显式 TODO 骨架 + return 默认值 + 注释"签名未在元数据/
    guide 确认,禁止编造";
  - 明确标准:库存查询/服务调用等外部 API,签名必须有来源(guide 检索命中/
    元数据确认/模板);无来源一律 TODO 占位,禁止凭记忆补全;
  - 说明为什么:编造 API 编译必挂,烧掉整条编译-修复循环(吃 w5 轮次与返工
    预算);TODO 占位编译通过,由后续元数据接线补全;
  - references/bill.md 补「服务调用不编造」要点与自检项;loader 摘要同步
    强化(无来源外部 API 一律 TODO 占位,禁止编造)。
- **knowledge-steward 强化 verify 建议必填**(评估发现:无 skill 蒸馏无
  proposed/verified 两态纪律):
  - 沉淀条目格式增加验证字段:proposed 态必填 —— 复现方式或人工确认人;
  - 明确无 verify 路径的沉淀不要写(一次性/无法复现的观察不沉淀);
  - 解释为什么:proposed 无 verify 路径 = 污染风险(幻觉修复被当知识),
    验证建议是防污染的收口(propose 时填的验证字段 = 后续 review 的作业清单);
  - references/distillation.md 条目模板/好例坏例/判据同步;loader 摘要补
    "proposed 必带验证建议"。
- 全量测试:272 全绿(契约断言短语未变,仅内容增强)。

---
## v1.19.0 — 2026-08-10(环境类错误升级 BLOCKED + w5 方法论摘要兜底)

### 行为变更

- **环境类编译错误不再空转修复轮次**(Gap A):经验库条目新增 `category`
  元数据(code=代码可修 / env=编译环境配置问题)。种子中 CS1056(C# 6 语法需
  Roslyn)/ MSB4067 / MSB3274 / MSB3275(框架不匹配)/ TimeoutExpired 标记
  `env`,其余代码类条目显式 `code`;`ExperienceStore.propose` 新增可选
  `category="code"` 参数(向后兼容,w7/DEPLOY/ARTIFACT 通道默认 code),
  `seed_load` 灌入时随元数据入库并透传检索。
- **w5 升级语义**:`_retrieve_fix` 命中 `category="env"` 时不再进入正常修复
  循环 —— 立即返回 BLOCKED,concerns 附运维提示(聚合首 2-3 条 env 命中,
  如"CSC_TOOL_PATH 指向 Roslyn / 提升 TARGET_FRAMEWORK / 放宽超时 / 换端口")。
  环境问题修代码无意义:不计编译轮次、不扣返工预算、LLM 不参与
  (编译客户端只调 1 次即停)。
- **w5 修复 LLM 方法论摘要兜底**(Gap B):`_llm_fix` 系统提示注入
  `COMPILE_FIXER_SUMMARY`(方法论在 compile-fixer skill + 环境类错误不修码
  报告 BLOCKED 提示运维),LLM 不主动调 load_skill 也持有核心方法论;
  loader 的 compile-fixer 摘要同步补充环境类说明。

### 文档

- **dev-standards.md §7.6「Windows 远程部署与运维」**(kingdee 编译服务实测 9 条):
  scp 多文件必须逐个指定目标路径;ssh 传参三层转义用 PowerShell
  `-EncodedCommand`(base64 UTF-16LE)规避;PowerShell 输出设
  `[Console]::OutputEncoding=UTF8`;Server 2016 无 Add-WindowsCapability
  (OpenSSH 用 MSI);uvicorn factory 必须显式 `--factory`;schtasks 保活 +
  环境变量经 bat 传递;改代码后 taskkill 全杀再重启;PowerShell 5.1 无 BOM
  UTF-8 乱码(注释用英文或带 BOM);金蝶服务器 8000 端口被占(编译服务换端口)。
- **compile-fixer SKILL.md 新增「环境类 vs 代码类(判别与升级)」**:判别线索 +
  升级路径(BLOCKED 附运维提示,不进修复轮次);errors.md 修复纪律补第 7 条
  (环境类升级不修码);knowledge-steward 检索路由表 experience 行补 env 升级注。
- 全量测试:新增 5 个测试(w5 环境类 BLOCKED 不空转 / 多命中聚合 / 代码类
  路径不变 / 系统提示含摘要;propose 携带 category;seed 断言补 category
  元数据),267 → 272 全绿。

---
## v1.18.0 — 2026-08-10(Windows 编译经验沉淀 —— Roslyn/编译环境方法论)

### 新增功能

- **Roslyn 编译器支持(代码已随 1bfcf8f 落地,本版补知识沉淀与文档)**:`CSC_TOOL_PATH`
  环境变量指向 Roslyn csc 目录 → 编译服务在 csproj 写入 `CscToolPath`/`CscToolExe=csc.exe`
  —— Framework 自带 csc 仅支持 C# 5,真实插件代码的字符串内插等 C# 6+ 语法必配;
  编译超时 180s → **300s**(Roslyn 冷启动 + ~30 引用 DLL 首次解析慢);
  csproj 补 System.Configuration 引用。E2E:完整真实插件项目(5 文件,含共享类)
  编译通过 + DLL 产出。

### 经验沉淀

- **种子 compile_errors.json +3 条(10 → 13)**:CS1056(意外的字符$,Framework csc
  不认 C# 6+ 插值语法 → 配置 Roslyn CSC_TOOL_PATH)、MSB4067(CscToolPath 写成
  Project 直接子元素 → 必须包 PropertyGroup)、TimeoutExpired(首次编译冷启动慢 →
  后端编译超时放宽 ≥300s,仍超时按实际调大);MSB3274/3275 既有条目已含
  TARGET_FRAMEWORK 修法,无重复。
- **compile-fixer SKILL.md 新增「编译环境要点」节**(方法论层,不写具体错误码):
  无 VS 环境 = Framework MSBuild + 旧式 csproj;C# 6+ 语法需要 Roslyn
  (CSC_TOOL_PATH);目标框架必须 ≥ 金蝶 BOS DLL 框架(否则引用被静默跳过);
  首次编译冷启动慢(超时放宽 ≥300s);csproj 属性必须包 `<PropertyGroup>`。
- **windows-deployment.md 故障排查新增 §10.5**(CS1056 → 配置 CSC_TOOL_PATH;
  MSB4067 → PropertyGroup;编译超时 → 后端 300s + agent 侧 120s 误判提示),
  原 §10.5 → §10.6;§11 注意事项编译时间同步 300s。
- **manual.md**:FAQ 新增 Q13(CS1056 → CSC_TOOL_PATH)/ Q14(编译超时 → 300s +
  agent 侧超时),Q9 后端超时 180s → 300s,§1.2 种子输出「新增 10 条」→「新增 14 条」。
- tests seed 断言注释同步(10 → 13 条,断言 n1>=10 不变仍通过)。

---
## v1.17.0 — 2026-08-09(编译服务多文件编译支持)

### 新增功能

- **`POST /compile` 支持多文件编译**(向后兼容,单文件形态不变):
  - 请求形态二选一:`{"code": ..., "project_name": ...}`(旧,等价
    `files=[{name: "Plugin.cs", code}]`)或 `{"files": [{name, code}, ...], "project_name": ...}`(新)。
  - 文件名校验:`^[A-Za-z0-9_][A-Za-z0-9_.-]*\.cs$` 白名单(仅叶子名,防路径穿越
    写 tmp 之外 / `-` 开头开关注入 / 非 .cs 覆盖 csproj)+ 重复名拒绝 + 至少一个文件;
    校验在 backend.compile 之前(非法请求不触达后端)。
  - `compile_service/models.py`:新增 `CompileFile(name, code)` dataclass + `resolved_files(req)`
    助手(files 显式给出则用之,否则退回单文件 Plugin.cs)。
  - `compile_service/server.py`:CompileRequest 新增 `files` 字段(code 变可选),`_FILE_NAME_RE` 白名单。
  - `compile_service/backends/msbuild.py`:`compile(files, project_name)` 每文件写入 tmp +
    csproj `<Compile Include="X.cs" />` 每文件一条(单文件 = 原行为);名称纵深防御
    (直调后端也不能逃逸 tmp)。
  - `compile_service/backends/mock.py`:`compile(files, project_name)` 规则对**全部文件源码拼接**
    命中(跨文件命中,file 字段仍来自规则);`protocol.py` 签名同步。
  - `agents/kingdee_plugin_agent/tools/compile_client.py`:新增 `compile_files(files, project_name)`;
    `compile(code, project_name)` 保留并委托之(单文件路径 w5 无感)。
  - `tests/eval/run_eval.py`:`_compile()` 双契约分发(CompileClient.compile_files / 后端 compile(files)),
    评估级 MockCompiler 直调路径适配新协议。

### 测试

- 新增 11 个多文件单测:resolved_files 两态、mock 规则跨文件命中(坏代码放第二个文件)、
  文件名校验 5 种非法名 → 400(后端不被调用)、重复名 → 400、files/code 皆空 → 400、
  server files 载荷往返(错误来自第二个文件)、client compile_files 往返、msbuild 多文件
  写盘 + csproj 多 Compile Include(单文件仍一条)、直调后端路径穿越名 → ValueError;
  全量回归 268 passed(含既有单文件全部用例原样通过)。

---
## v1.16.0 — 2026-08-09(编译服务 Windows 部署全配置化 —— 零硬编码路径)

### 变更

- **compile_service 部署路径全配置化**(环境变量覆盖 + 代码相对默认,零硬编码 Windows/容器路径):
  - `compile_service/backends/msbuild.py`:`default_msbuild_path()` 探测优先级
    `MSBUILD_PATH` env → PATH 的 msbuild(VS 环境)→ `FRAMEWORK_MSBUILD_PATH` env →
    硬编码 Framework 自带路径(最后兜底);`artifact_dir` 缺省从 cwd 相对
    `data/kingdee-compiled` 改为**代码相对** `仓库根/data/kingdee-compiled`
    (compile_service/backends/msbuild.py 上溯 3 层),构造函数仍可覆盖。
  - `compile_service/server.py`:`REFS_DIR` 缺省从容器路径 `/app/references` 改为
    **代码相对** `compile_service/build/references`(Windows 原生部署与容器 /app 挂载均可用);
    新增 `COMPILE_ARTIFACT_DIR` env → 透传 MsbuildCompiler.artifact_dir;
    `MSBUILD_PATH` 现由后端直接读 env(独立于 server.py 参数)。
  - `compile_service/Dockerfile`:显式 `ENV REFS_DIR=/app/references`,保持容器布局
    契约不变(镜像内 references 固定 /app/references,避开代码相对默认值接管)。
  - `compile_service/fetch_kingdee_dlls.ps1`:新增 `KINGDEE_BIN_DIR` 环境变量提供
    金蝶 WebSite\bin 源目录(-SourceDir 参数仍最优先,env 后于参数、先于自动探测)。
  - 文档:windows-deployment.md(start_compile.bat 改 `%~dp0` 相对 + 全量 env 表 +
    PORT/HOST 约定说明)、manual.md、agent CLAUDE.md、.env.example 同步。

### 测试

- 新增 6 个配置化单测:`default_msbuild_path()` 三态(MSBUILD_PATH env / FRAMEWORK_MSBUILD_PATH
  env / 硬编码兜底 intact)、`artifact_dir` 缺省代码相对、`_backend_from_env` REFS_DIR
  缺省代码相对 build/references、COMPILE_ARTIFACT_DIR env 透传;全量回归通过。

---
## v1.15.0 — 2026-08-09(RAG embedding 模型配置化 + 切换远程服务重灌)

### 新增功能

- **RAG embedding 模型配置化**(`common/rag.py::_embedding_model`,lru_cache
  单例保留):`EMBEDDING_*` 环境变量组经 common.config 读取(.env 同源):
  - `EMBEDDING_PROVIDER` = `huggingface`(默认,本地 sentence-transformers,
    离线可用)| `openai-compatible`(远程 OpenAI 兼容 embedding 服务,经
    langchain-openai 的 OpenAIEmbeddings 接入,延迟导入);
  - `EMBEDDING_MODEL` 缺省:huggingface 用 `BAAI/bge-small-zh-v1.5`(512 维),
    openai-compatible 用 `Qwen/Qwen3-Embedding-8B`;
  - `EMBEDDING_BASE_URL`:**openai-compatible 必填**,缺失抛清晰错误
    (RagError,不静默回退);
  - `EMBEDDING_API_KEY` 可选,默认空;免鉴权服务自动传占位符 `not-needed`
    (langchain-openai 校验要求非空);
- **切换团队远程嵌入服务并全量重灌**:`.env` 配 `openai-compatible` +
  `http://10.33.17.234:32320`(openclaw memorySearch)+ Qwen3-Embedding-8B;
  drop `data/kingdee-rag` 后重灌三集合 —— 维度 512 → **4096**(Qwen3-Embedding-8B,
  实测远程 POST /v1/embeddings);hybrid_search 冒烟通过(guide "插件开发" 命中
  熊说知识库 + 内部 skill;api_ref "BusinessDataServiceHelper" bm25=0.7 首位
  命中星空企业版开发笔记)。

### 集合灌入(2026-08-09 切换后终态,重跑新增 0)

- guide 75 chunks / 28 源(内部 skill 54 = 上版 53 + maintenance.md §5 新增
  1 + 官方 6 页 21);api_ref 4 chunks / 3 源;experience 10(种子)。
- **活页漂移复现**:BOS FAQ 精选页(685345938776315392)重跑 +2,属源侧内容
  更新(既有结论,非管线缺陷),其余全部 +0。

### 文档

- `.env.example` 新增 RAG 嵌入模型配置组(EMBEDDING_* + 远程示例 + 换模型
  重灌警告);
- `agents/kingdee_plugin_agent/CLAUDE.md` 常用操作新增「配 embedding 模型」
  (配置项 + 换模型必须 drop 重灌);
- `knowledge-steward/references/maintenance.md` 新增 §5「更换 embedding 模型
  (全量重灌)」:删库 → 三集合重灌命令 → 验证(冒烟/维度/幂等 +0/全量测试)。

### 测试

- 新增 `tests/conftest.py`:autouse 夹具清除 `EMBEDDING_*` env + 清
  `_embedding_model` 缓存 —— 测试环境隔离(真实 .env 配远程服务时,
  RagClient 测试仍确定性走 huggingface 本地默认,不依赖网络);
- `tests/test_rag.py` 新增 8 项 env 分支测试:huggingface 默认/自定义模型、
  openai-compatible 默认模型+base_url、自定义 model+api_key 透传、缺
  `EMBEDDING_BASE_URL` 抛错、未知 provider 抛错、空模型名回落默认
  (huggingface/openai-compatible 两分支);全套 **249 passed**(241 基线 + 8 新)。

### 修复

- **未知 `EMBEDDING_PROVIDER` 不再静默回退 huggingface**:拼写错误(如
  `openaicompatible`)直接抛 RagError,点名支持值 —— 静默回退会把误配置
  当成本地模型,检索静默失真;
- **`EMBEDDING_MODEL=` 空串回落默认**:空串(显式置空)与未配置等价,
  不再产生 `model_name=""` 的空模型构造(与 api_key 的 `or 占位符` 同一写法)。

---
## v1.14.0 — 2026-08-09(RAG 导入管线 + guide/api_ref 集合灌入)

### 新增功能

- **RAG 导入管线 `tools/ingest.py`**(URL/目录双入口 + CLI,零新增依赖):
  - `ingest_url(url, collection, title="")`:httpx 抓取(30s 超时 + 浏览器 UA)→
    stdlib html.parser 提取正文(剔除 script/style/nav/header/footer 噪音)→
    代码感知分块 → RagClient 入库(metadata: source/title/collection),
    返回新增 chunk 数;
  - `ingest_dir(dir, collection)`:递归 *.md,自动去 YAML frontmatter,相对路径
    作 source,单文件失败跳过继续,**全部失败才报错**(不静默全跳过);
  - `code_aware_chunk(text, max_chars=1500)`:段落边界切块(段落空行保留);
    代码围栏(```)**无论多长整体独占一个 chunk,绝不在围栏内部切分**(未闭合
    围栏也保留);超长段落按句末标点(。！？!?;；)兜底切分;行首缩进保留
    (HTML &lt;pre&gt; 代码行经分块不丢缩进);
  - `normalize_title(url, html=None)`:&lt;title&gt; → 首个 &lt;h1&gt; → URL 尾段
    三级回退;**仅剥离已知站点名后缀**(" - 金蝶开发者社区" 等,不按任意
    分隔符截断 —— "金蝶云·星空-BOS平台" 中的 - / · / | 是合法标题字符);
  - **幂等是"去重式"而非同步式**:按 source + 文本查重,同 source 且**内容未变**
    重跑新增 0;内容变更后重跑会新增、新旧版本并存 —— 编辑已灌入文档须先删旧
    重灌:`--delete-source <source> --collection <库>`(删除该 source 全部条目)
    再重灌;
  - **&lt;pre&gt; 代码块缩进保留**:HTML 提取按 pre 感知处理 —— 代码行原样
    保留缩进/结构,非代码行折叠空白;未闭合 &lt;pre&gt; 毒化兜底(后续块级
    标签即退出 pre 模式,后续文本正常清洗);
  - **动态行样板覆盖**:裸数字/逗号数字行(浏览计数 "4,457")、赞/删除/收起/
    取消交互行、编辑于/发布于时间戳、浏览/赞赏计数 —— 全部整行剔除;
    **重发布期数前缀【第N期】剥离**(正文与标题,重发布不改变正文文本);
    9 个官方 URL 双次抓取 diff 验证文本稳定;
  - CLI:`--url <URL>`(可重复)/ `--dir <目录>` / `--seed-internal` /
    `--delete-source <source>` + `--collection api_ref|guide|experience`,
    `--data-dir` 可改数据目录;单 URL 失败打印明确原因(HTTP 状态/超时/无正文)、
    全部失败退出码 1。

### 集合灌入(data/kingdee-rag,gitignored;2026-08-09 终态实跑,重跑新增 0)

- **guide 72 chunks / 27 源**:内部 skill 7 份 SKILL.md + 14 份 references
  (design-builder / code-generator / code-reviewer / compile-fixer /
  knowledge-steward / requirement-clarify,53 chunks)+ 金蝶官方 6 页(BOS 平台
  知识地图、星空 BOS 平台简介、熊说金蝶 BOS 知识库、BOS FAQ 精选、收款单扩展
  实操、AI 辅助二开,19 chunks);
- **api_ref 4 chunks / 3 源**:金蝶官方 3 页(星空企业版开发笔记 —— 含
  BusinessDataServiceHelper/DBServiceHelper 用法、WebAPI 多选基础资料、WebAPI
  系统集成主题);
- 模板类(`templates/*.cs`)不入库 —— 代码模板由 w3 直接使用,无需检索;
- **活页漂移结论**:BOS FAQ 精选页(685345938776315392)是人工持续策展活页,
  正文随编辑在源侧变化,重跑偶发 +1~3 属源侧内容更新(非管线缺陷),
  刷新用 `--delete-source` + 重灌;其余 8 个官方页与内部文档重跑稳定 +0。

### 测试

- 新增 `tests/test_ingest.py` 29 项(全套 212 → **241**):代码围栏跨段落整体
  保留/超长围栏不切分/未闭合围栏保留、长段落句末切分无内容丢失、HTML 噪音
  (script/nav/分享收藏)剔除 + **&lt;pre&gt; 缩进保留 + 未闭合 pre 不毒化
  后续文本 + 段落空行保留(分块段落边界)+ 动态行(裸数字/赞删除收起/编辑于)
  剔除 + 【第N期】前缀剥离**、ingest_dir tmp 目录入库可检索 + frontmatter
  剔除 + 去重幂等、**编辑后重跑重复 → delete_source 删旧重灌干净**、
  ingest_url mock HTTP 入库 + HTTP 错误明确消息、**fetch_html 真实异常映射
  (超时/HTTP 状态/网络错误 → IngestError)**、CLI --dir 可运行 / 单 URL 失败
  退出 1 / 多 URL 部分失败继续 / --delete-source / 无参数退出 2。

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
## v1.13.0 — 2026-08-09(E2E 门达成 —— 真实金蝶环境编译全通,部署/种子/文档同步)

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
## v1.12.0 — 2026-08-09(下发模板补验收标准/上限字段 + 设计文档 14→8 worker 偏差同步)

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
## v1.11.0 — 2026-08-09(死代码清理 + 冒烟链路 form_id/DLL 传递 + 反馈端点 + --env 记录)

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
## v1.10.0 — 2026-08-09(P2 五项 —— 指标/失败收尾包/JSON 重试/records 接线/.env)

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
## v1.9.0 — 2026-08-08(时间预算 + 需求版本冻结,设计 §8 两项落地)

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
## v1.8.1 — 2026-08-08(三份文档:项目/技术/使用手册)

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
## v1.4.0 — 2026-08-08(全流程交付首版:CLI + Web + 文档)

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
## v1.3.0 — 2026-08-08(主管图构建 C10)

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
