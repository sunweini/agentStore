# 版本更新说明(CHANGELOG)

项目:agentStore — 基于 LangChain/LangGraph 的多步骤任务 Agent 组
仓库:https://github.com/sunweini/agentStore

---

## v1.5.0 — 2026-08-08(load_skill 机制:requirement-clarify 渐进式披露,对照 sentiment 模式)

### 新增功能

- **skill 渐进式披露(skills/loader.py)**:`load_skill(skill_name)` 工具(摘要启动加载,按需取 SKILL.md + 三套类型模板;requirement-clarify 无 references/ 子目录,模板直放 skill 目录)+ `skill_summary()` 摘要注入 + `SKILL_HINT` 提示常量;未知 skill → error JSON 并列出可用项
- **worker 绑定**:w1-w5 五个 LLM 调用点改经 `structured_with_skill` 绑定 load_skill —— 官方 `tools` 参数(json_schema + include_raw,经安装包 introspection 核实:bind_tools 后再 with_structured_output 会经 `__getattr__` 委派丢失 tools,必须用 tools= 形态),最多 2 回合(回合 1 调工具 → 喂回 ToolMessage → 回合 2 出 schema);脚本/fake LLM(无 bind_tools)自动跳过绑定,既有测试契约不变
- **w1 澄清 prompt 注入 skill 摘要**:摘要 JSON 走模板变量占位(dev-standards §7.2 f-string 花括号陷阱)
- **技能文档**:`skills/requirement-clarify/SKILL.md`(一次一问/多选优先/元数据驱动/10 轮上限/决策+假设记录,引用 bill/service/list 模板)

### 测试

- tests/test_kingdee_agent.py 新增 3 项(load_skill 返回 SKILL.md+三模板 / 未知 skill 报错 / skill_summary / 工具回合→schema 回合);全套 149 项全过

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
