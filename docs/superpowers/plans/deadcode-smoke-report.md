# kingdee-plugin-agent:死代码清理 + 冒烟链路(form_id/DLL)+ 反馈端点 + --env 记录 — 执行报告

日期:2026-08-09 · 基线:8b42237(185 tests green) · 交付:201 tests green(16 新增)

## 一、死代码清理(逐项:验证 → 动作 → 测试证据)

### 1. `Supervisor._check_budget`(supervisor.py)
- **验证**:grep 仅 docstring(第 76 行)与定义处出现,无调用;`_decide` 第 4 步内联预算判定(`rework_budget_left <= 0 and remaining` → fail)已覆盖等价逻辑。
- **动作**:删除方法 + docstring 行。
- **测试**:`test_budget_exhausted` 改写为断言 `decide()` 返回 `fail:返工预算耗尽` 且剩余子任务标记 failed(等价语义,不再测死方法)。

### 2. `RequirementWorker.interrupt_message`(w1_requirement.py)
- **验证**:无调用。agent.py w1_node 内联逻辑(第 261-274 行)产出带 `type` 字段的 payload dict(`question`/`confirm`),API/CLI 按 `payload["type"]` 分支 —— 内联才是 interrupt 契约;方法返回裸字符串且无 type 包装,不如内联。
- **动作**:删除方法(内联逻辑保留,契约更完整)。
- **测试**:`test_w1_interrupt_message_and_record_answer` 改写为 `test_w1_record_answer_and_build_spec`(record_answer + build_spec 决策组装 + 确认摘要)。

### 3. `NEEDS_CONTEXT` 状态
- **验证**:代码 grep 零引用(仅设计文档 specs/plan 提及);不在 `TASK_STATUS`;无任何代码分支。
- **动作**:**保留设计契约值**,在 base.py `_report` docstring 注明「当前无产出路径,先接通 supervisor 处理再加,勿裸用」。选择理由:它是设计上报契约的一部分,删掉会破坏与设计文档的对应,注释防未来误用成本最低。
- **测试**:无(纯注释)。

### 4. `blocked` 状态
- **验证**:在 `TASK_STATUS` + `STATUS_TO_WORKER("blocked" → "w1")`,全代码无写者(仅测试构造)。`_ready_batch` 对 blocked 排除派发(防 supervisor↔dispatcher 忙循环,已有 C10 实测注释)。
- **动作**:**保留防御处理**,STATUS_TO_WORKER 的 blocked 条目加注释:当前无写者、防御保留,守卫未来 worker 置"缺用户信息"态。
- **测试**:既有 `test_next_ready_no_shadow_by_blocked_dep` 保持通过(防御行为仍在)。

### 5. 死 prompt 文件 `prompts/w5_5_smoke.md` + `prompts/w6_package.md`
- **验证**:SmokeWorker/PackageWorker 不加载任何 prompt(无 LLM 分支);grep 无代码/测试/tech.md/manual.md 引用(命中均为 worker 模块名 w5_5_smoke.py / 设计文档文件树)。
- **动作**:删除两个文件。
- **测试**:全套通过(无引用断裂)。

### 6. `state.py` docstring 的 `ask_question`
- **验证**:TaskState 无此字段。
- **动作**:修正为实际字段(action/dispatch_id/user_feedback/metrics + started_at/spec_version)。
- **测试**:无(纯注释)。

## 二、冒烟链路修复(结构级;真实 DLL 仍属 P1 真实环境)

### 7. form_id 恒空 → w1 确认时提取进 state.environment["form_id"]
- **根因**:CLI/API 初始 state 无 `environment` 键(TaskState 默认空 dict),w5.5 读 `state.environment.get("form_id", "")` 恒空。
- **动作**:
  - `PlanOutput` 新增 `form_id` 显式槽(LLM 拆解时归纳),`split_subtasks` 回写 `spec["form_id"]`;
  - `RequirementWorker.extract_form_id(spec)`:显式槽优先 → 兜底从 decisions 中"formid/单据/表单"相关问题的答案取首个标识符 token(llm=None 确定性路径同可用)→ 无则空串;
  - `_confirm_and_split` 提取后写入 `environment["form_id"]`(只增不改,保留 env_name)。
- **测试**:3 项 —— 显式槽、decisions 兜底(含无单据问题 → 空)、LLM 拆解回写;图全链路补断言 `r["environment"]["form_id"] == "SAL_SaleOrder"`。

### 8. DLL 传递(CompileResult → subtask.dll_path → w5.5 冒烟 / w6 打包)
- **根因**:w5 编译成功从不持有 DLL 路径;w5.5 拿 `Path(code_path)`(源码 Plugin.cs)冒充 DLL;w6 `dll_path` 恒空串。
- **动作**:
  - `models.CompileResult` + `dll_path` 字段;
  - 真实 msbuild 后端:编译成功后把输出 DLL(临时目录编译完即删)复制到服务端留存目录 `artifact_dir/<project_name>/Plugin.dll`,`result.dll_path` 返回留存路径;mock 后端无产出 → 空串;
  - `server.py`:`/compile` 响应带 `dll_path`;新增 `GET /dll/{project_name}` 流式返回二进制(project_name 过白名单,防路径穿越;路由层对 `../` 规范化拦截 404);
  - `CompileClient._fetch_dll`:成功且服务端有产出 → 拉到本地 `artifact_dir/<project_name>/Plugin.dll`,`dll_path` 为本地路径;拉取失败 → 空串(优雅降级);
  - `Subtask.dll_path` 新字段;w5 成功时存值;w5.5 验证对象改为 DLL,无 DLL → `DONE_WITH_CONCERNS`「无 DLL(编译后端未产出),跳过部署验证」,不扣预算、不计冒烟指标;w6 `deliverable["dll_path"] = subtask.dll_path`(打包器已有 bin/ 写入逻辑)。
- **测试**:5 项 —— w5 存 dll_path(有/无后端产出)、无 DLL 跳过(不调冒烟客户端/不扣预算/不计指标)、dll_path 传至冒烟、w6 入包 bin/Plugin.dll(无 DLL 时无 bin/ 条目)、编译服务(/compile 响应、GET /dll 下载/404/路由层拦截、白名单单测、客户端拉取到本地、拉取失败降级空)。
- **指标语义影响(有意变更)**:mock 后端无 DLL → 冒烟跳过不计数,`smoke_pass_count` 相关 2 项图测试断言更新(0),接真实后端后恢复计数;tech.md 已注明。

## 三、反馈端点(设计 §12)

- **动作**:`TaskHandle.record_feedback(reason)`(经验库 `propose("DEPLOY", sha256(reason)[:12], reason, "…反馈通道")`,proposed 态,失败只记日志不阻塞)+ `POST /tasks/{id}/feedback`(apikey 鉴权、404 未知任务、SSE 发 feedback 事件)。
- **测试**:2 项 —— 未知任务 404 + 无 apikey 401;真实 ExperienceStore 两个不同原因累计 2 条 + 相同原因去重 + proposed 态。
- **文档**:manual.md §4 端点表 + api.py 模块 docstring;tech.md 无 §12 小节,补入 §11(反馈通道说明)。

## 四、--env 消费(最小化)

- **动作**:CLI/API 初始 state 新增 `environment: {"env_name": …}`(此前 environment 恒空 dict,节点不可见);只记录,不做多环境切换。
- **测试**:2 项 —— CLI 初始 state(SpyGraph 捕获首次 invoke dict)、API `handle.state["environment"] == {"env_name": "test"}`。
- **债务措辞更新**:agent CLAUDE.md + tech.md §11 `--env 未消费` → `部分消费`。

## 五、延后项(未实现,仅记录)

- **官方文档爬取/内部资料导入管线**:需外部资源(金蝶官方文档源 + 内部资料权限),属基础设施项,建议另立计划。
- **版本兼容矩阵**:需真实金蝶环境验证后建立,当前 E2E 门未解锁。
- **API 任务持久化**:建议 —— v1 用**文件持久化**(每任务 JSON 快照,含 todo/requirement_spec/metrics/acceptance,启动时扫描恢复,天然适配单机单进程;SQLite 引入存储层复杂度,收益小;纯内存不可接受用于真实交付)。恢复语义需先定:恢复后图继续跑 vs 仅可查历史(建议后者,v1 免 checkpointer 恢复复杂度)。
- **多环境切换**:需真实环境;现 env_name 已记录,为切换留了槽位。

## 六、测试与文档

- 全套 `.venv/bin/python -m pytest tests/ -q`:**201 passed**(185 基线 + 16 新增),无失败。
- tech.md(冒烟行/w6 产物/environment 字段/§8.2 编译服务接口/§11 债务+反馈通道)、manual.md(§4 端点表/FAQ Q7/§6 zip 树/§7 单环境)、agent CLAUDE.md(Subtask 契约 + 债务 5)、CHANGELOG v1.11.0。
- 顺带修正:manual.md FAQ Q7 中"records 未接线恒空"表述已在 v1.10.0 修复,本次一并更新(同一句内)。

## 七、遗留关注

- 真实 msbuild 后端 DLL 链路(留存/下载/冒烟/入包)未经真实环境验证 —— P1 真实 DLL 到位后按 E2E 门验证。
- 冒烟跳过语义(mock 模式)使 smoke_pass_count 恒 0,真实指标口径以真实后端为准;如需 mock 模式可观测性,可后续加"skip_count"指标(本次未加,避免指标契约膨胀)。

---

## 八、评审修复(1 Important + 2 Minors,commit 267bdb3 之后追加)

### Important — POST /compile 写侧 project_name 路径穿越
- **问题**:白名单只守 GET /dll 读侧;POST /compile 的 project_name 未校验即拼进 `artifact_dir/<project_name>/Plugin.dll` 并 `mkdir(parents=True)` —— 构造 `../../references` 可在编译容器(端口暴露到宿主机)任意目录写文件。
- **修复**:`compile_endpoint` 入口套同一 `_PROJECT_NAME_RE` 白名单,非法 → 400(`backend.compile` 之前,后端不执行、不落盘)。
- **测试**:`test_compile_rejects_bad_project_name_no_file_written`(真实 MsbuildCompiler + artifact_dir;`../../references` → 400 且 artifact_dir 不存在;合法名仍 200)。

### Minor 2 — extract_form_id 兜底可能取到 "FormId" 词本身
- **问题**:答案如"单据 FormId 是 SAL_SaleOrder"时,首个标识符 token 是 "FormId"。
- **修复**:`finditer` 遍历 token,`token.lower() != "formid"` 才接受(大小写不敏感跳过);全为 FormId 词 → 空串。
- **测试**:`test_w1_extract_form_id_from_decision` 追加两断言(跳过 FormId 词取 SAL_SaleOrder;纯 FormId 词 → "")。

### Minor 4 — CompileClient._fetch_dll 只捕 httpx.HTTPError
- **问题**:`write_bytes`/`mkdir` 的 OSError(磁盘满/权限)未捕获 → 异常传播打崩 w5 节点。
- **修复**:`except (httpx.HTTPError, OSError)` → dll_path 空串(降级契约)。
- **测试**:`test_client_dll_fetch_oserror_degrades_to_empty`(artifact_dir 是已存在文件 → mkdir 抛 FileExistsError(OSError 子类)→ dll_path == "")。

### 结果
- 全套 `.venv/bin/python -m pytest tests/ -q`:**203 passed**(201 + 2 新增测试函数;form_id 断言并入既有测试)。
- 提交:`fix(compile): /compile project_name 白名单 + form_id 提取跳过 FormId 词 + DLL 拉取 OSError 降级`。
