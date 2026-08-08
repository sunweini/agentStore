# 版本更新说明(CHANGELOG)

项目:agentStore — 基于 LangChain/LangGraph 的多步骤任务 Agent 组
仓库:https://github.com/sunweini/agentStore

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
