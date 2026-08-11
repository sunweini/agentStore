# AI Agent 开发规范(基于 LangChain)

版本:1.0
适用范围:本项目中**所有** agent 的开发(现有 sentiment-query-agent 及后续 agent2、agent3…… 及任何 agent 功能)。

## 1. 核心铁律:必须依据 LangChain 官方文档与 API 指引开发

**任何 agent 开发都必须参考并依据 LangChain 官方文档和 API 指引,禁止凭记忆写 API 用法。**

具体执行:

1. **开发前查文档**:开始任何 agent 功能开发前,先用 langchain MCP 查文档确认 API 用法:
   - `docs-langchain` MCP — 官方文档全文搜索与阅读(如 LangGraph 图结构、ToolNode、state 定义、application-structure 等)
     - `search_docs_by_lang_chain`:搜索文档
     - `query_docs_filesystem_docs_by_lang_chain`:按路径读取具体文档页面
   - `reference-langchain` MCP — API 参考(类/方法签名、参数、示例)
     - `search_api`:搜索符号
     - `get_symbol`:获取符号完整文档(签名/参数/示例)

2. **API 用法与文档冲突时,以文档为准**:实现中如遇 API 行为与预期不符,回查官方文档确认正确用法,修正实现并记录(例:`recursion_limit` 是运行时 config 参数,不是 compile 参数 — 官方 graph-api 文档确认)。

3. **文档链接留痕**:模块 docstring 中注明引用的官方文档链接,便于后续维护者溯源。

4. **新增功能先查再写**:新引入 LangChain/LangGraph 能力(如 checkpoint、streaming、多 agent 协作)时,先查官方文档对应章节,再设计实现。

## 2. 技术栈约束

- 语言:Python
- 编排框架:LangGraph(StateGraph 循环图),不用 AgentExecutor
- LLM 接入:经 langchain 官方集成包(`langchain-openai` / `langchain-anthropic` 等),不手写 HTTP 调用
- 工具定义:langchain `@tool` 装饰器,返回值用字符串(不用结构化对象)

## 3. 架构规范

- **多供应商模型**:LLM 支持多供应商、多模型 ID。供应商注册表模式(`common/llm.py`),换供应商改 `.env` 不改代码。
- **prompt 管理**:prompt 与代码分离,存 `agents/<agent>/prompts/*.md`;加载走 `common/prompts.py`。一个 agent 可一个 system prompt,也可按 node 拆多 prompt(能力支持,按需使用)。
- **agent 目录**:`agents/<agent>/` 含 `agent.py`(图构建)、`utils/{state,nodes,tools}.py`、`prompts/`。新 agent 在 `langgraph.json` 注册。
- **每个 agent 必须有独立 CLAUDE.md**:`agents/<agent>/CLAUDE.md` 是该 agent 的专属开发指南,在该 agent 目录下工作时自动加载。新建 agent 时**必须按 §6 模板生成**,并写入根 `CLAUDE.md` 的架构约定。
- **共享层**:跨 agent 复用代码放 `common/`(模型工厂/配置/prompt 加载/基础工具)。

## 4. 开发流程

1. 设计先行:新 agent/新功能先出设计,用户确认后实现。
2. 骨架阶段只建目录+占位文档(不写实现代码),用户确认后填实现。
3. **按 §6 模板生成 `agents/<agent>/CLAUDE.md`**。
4. 实现后测试:`pytest`,三层测试(工具单测/图单测 mock LLM/端到端)。
5. **每次开发收尾必须更新 [CHANGELOG.md](../CHANGELOG.md)**:按版本追加新功能/修复/变更,测试通过 + commit 推送后完成。版本号递增规则:功能新增 = 次版本(+0.1.0),修复 = 补丁(+0.0.1),正式里程碑 = 主版本。

## 5. 质量要求

- 测试覆盖工具调用链路(agent→tools→agent→END)。
- 端到端测试无 key 自动跳过,不阻塞本地无 key 环境。
- 密钥不硬编码,统一 `.env` + `common/config.py`。
- 日志结构化(key=value),遵循可观测性规范。
- **全链路可观测**:所有 agent 必须接入 OpenTelemetry 全链路监控——HTTP 请求、图节点执行、LLM 调用、工具调用都产生 span,携带 trace_id 关联;支持可视化查看(如 Jaeger/Grafana)。node/API 入口注入 trace context,错误记录到 span。

## 6. Agent CLAUDE.md 模板

**新建 agent 时,必须按此模板生成 `agents/<agent>/CLAUDE.md`**,替换 `<agent>` 占位符,并按实际架构填写。参考实例:`agents/sentiment-query-agent/CLAUDE.md`。

```markdown
# <agent> 开发指南

<一句话:本 agent 是什么、做什么。>

## 本 agent 是什么

- 职责:<本 agent 的核心职责>
- 开发前必读:根目录 [CLAUDE.md](../../CLAUDE.md) 和
  [docs/dev-standards.md](../../docs/dev-standards.md)(必须依据 langchain MCP 文档/API 开发)

## 架构

<图结构文字描述或 Mermaid 图,例:>
START → <node1> ──<条件>──→ <node2>
            │
            └────<条件>────→ END

| 文件 | 职责 |
|---|---|
| <agent>.py | <图构建说明> |
| utils/state.py | <状态定义说明> |
| utils/nodes.py | <节点/路由说明> |
| utils/tools.py | <工具定义说明> |
| prompts/<name>.md | <提示词说明> |

## 常用操作

- **加工具**:<步骤>
- **改提示词**:<步骤>
- **接真实业务**:<步骤>
- **跑测试**:<步骤>

## 约束

- <本 agent 特有的约束;通用约束见根 CLAUDE.md>
```

## 7. 通用开发经验(踩坑记录,开发 agent 前必读)

以下经验来自 sentiment-query-agent(海外舆情方案生成)与 kingdee-plugin-agent(Windows 远程部署)实际开发,均验证过。

### 7.1 LangGraph / checkpointer

- `AsyncSqliteSaver.from_conn_string()` 返回**异步上下文管理器**,必须 `async with` 进入后才拿到 saver 实例,不能直接当 saver 传给 `compile()`(否则报 "Invalid checkpointer")。
- `recursion_limit` 是运行时 config 参数(`graph.invoke(inputs, config={"recursion_limit": N})`),不是 compile 参数。
- 图节点内 LLM 调用必须用 `ainvoke`(异步)。用同步 `invoke` 会阻塞 FastAPI 事件循环,流水线跑时其他请求全部卡死(实测现象:progress 接口无响应)。
- 生成中进度从 checkpoint 读(`aget_state`,thread_id=group_id),完成后才读文件/草稿——不要在生成中读文件(会 404)。

### 7.2 LLM 输出格式(DeepSeek 实测)

- **必须用 JSON Mode**:`llm.bind(response_format={"type": "json_object"})`,prompt 须含 "json" 字样(官方要求)。否则 LLM 常输出 Markdown 代码块/说明文字,`json.loads` 直接失败。
- 即使开 JSON Mode 也**保留容错解析兜底**:剥 ```json 代码块 → 截取首个 `{` 到最后一个 `}` → json.loads。
- **ChatPromptTemplate 是 f-string 语法**:prompt 里 JSON 样例的 `{}` 必须转义成 `{{}}`,否则报 "Nested replacement fields are not allowed"。
- prompt 必须给 LLM **具体 JSON schema 样例**(字段名/枚举值),否则 LLM 盲猜字段名,脚本校验全挂(实测:轨 key 输成 "boolean" 而非合法枚举如 全量新闻/负面新闻/行业新闻/快讯/司法/招标)。
- LLM 输出的数组长度可能与上步不一致(如 schemes 数),下游合并要防御性取值(`i < len(...)` 保护),否则 `list index out of range`。
- **deepseek-v4-flash 推理模型注意**(生产实测 2026-08-11):
  - 思考模式默认开启,推理 token 计入 max_tokens 总预算;极端情况思考可能吃光预算(reasoning_tokens=65536 输出为 0)。
  - **不要传 `extra_body={"thinking": {"type": "disabled"}}`**:服务端对 thinking disabled 强制输出上限 8192 token,反而截断大 JSON 输出(实测 max_tokens=32768 无 thinking 参数可正常输出 22704;加 thinking disabled 后 8191 即截断)。
  - **结论:传 max_tokens=32768,不传 thinking 参数**,让模型自行控制思考量。
- **max_tokens 影响输出上限**:显式传 max_tokens 时服务端按该值截断(不传时可输出 14383)。给足余量(如 32768)避免截断。

### 7.3 skill 分步脚本模式

- skill 是知识源(方法论),**分步格式契约放脚本**(stepN.py):LLM 自由生成,脚本负责校验字段/标准化/补默认值/缺字段记 GAP(编号 GAP00N)。
- 脚本读 stdin JSON、写 stdout JSON,非 JSON 输入退出码非 0 + stderr 说明 —— 节点据此判断重试。
- 输出格式定义唯一来源:skill 的 `references/output-formats.md`,节点 prompt 引用它。
- **方法论供给用 load_skill 工具(方案 2a)**:每步节点绑定 `load_skill`(`bind_tools([...], strict=True)`),LLM 需要专业指导时主动调用,拿方法论后再生成。上限 2 回合(回合 1 并行调工具 → 回合 2 生成 JSON),防死循环。
- **DeepSeek JSON Mode + tool calling 兼容**:`bind_tools([...], strict=True)` 与 `response_format={"type":"json_object"}` 可同用(实测)。注意:工具必须 `strict=True`,否则报 "Only strict function tools can be auto-parsed"。
- **代码调用脚本 vs 文档指导**:运行时脚本调用放代码(确定性,方案 A);SKILL.md 文档写清脚本用法(知识层,自包含)。两者不冲突,缺一不可——只写文档不接代码,LLM 不会主动用脚本;只接代码不写文档,skill 无法独立交付。

### 7.4 数据与路径

- 运行时数据目录(方案组/计费/checkpoint)统一放项目根 `data/`,计算路径注意文件层级(子目录多一层 `parent`),**加路径单测防回归**。
- 计费 pending 记录有并发上限(防刷),测试遗留的 pending 会触发 429 —— 清理 `data/billing/` 即可。
- `data/` 运行时产物加 `.gitignore`,不提交。
- **Python 包名不能含连字符**:`from agents.sentiment-query-agent import x` 是 SyntaxError。目录名/展示名可用连字符(业务名),但**包名必须下划线**(`agents/sentiment_query_agent`)。改名时:目录 `git mv` + 内容 sed 替换 + import 路径逐一核对,避免 sed 误改展示名(文档/日志 service 名)与包名混用。

### 7.5 前端联调

- 后端必须加 CORS middleware,否则浏览器页跨域被拦。
- 前端轮询 progress 时,404 是"还没数据"的正常态,不要当错误处理;但要确认后端在生成中能返回实时进度(见 7.1)。
- 提交按钮要禁用 + loading 态,失败用 alert 醒目提示(小字错误提示用户注意不到,看起来像"没反应")。

### 7.6 数据库双后端(MySQL/SQLite)

- **占位符差异**:pymysql 用 `%s`,SQLite 用 `?`——统一在 db 层转换,业务 SQL 只写 `%s`。
- **语法差异**:SQLite 无 `NOW()`(用 CURRENT_TIMESTAMP)、无 `FOR UPDATE`(单写者场景可去掉)、`execute` 返回 cursor 而非行数(SELECT 需 fetch)。db 层统一适配。
- **SQLite 内存库每个连接独立**:`:memory:` 建表后新连接看不到表,测试用临时文件。
- **事务包装**:db.transaction 注入 `_exec`(自动转换占位符 + 统一返回值:SELECT 返回 dict 列表,其他返回影响行数),业务代码不直接碰 cursor.execute。
- 迁移脚本支持 **dry-run**(默认只报告不写库),先验证再执行。

### 7.6 Windows 远程部署与运维(kingdee 编译服务实测)

- **scp 多文件必须逐个指定目标路径**:`scp a b user@host:dir/` 会把 `b` 当目标路径、`a` 复制成 `dir/b`(不是并排放两个文件)。对策:每个文件单独 `scp a user@host:dir/`、`scp b user@host:dir/`,或先 scp 到临时目录再在远端 `mv`。
- **ssh 传参经 cmd/PowerShell 三层转义,引号易错**:本地 shell → ssh → 远端 cmd/PowerShell,每层吞一层引号,嵌套 `'""...'` 极易写错还难排查。对策:改用 PowerShell `-EncodedCommand`(命令转 UTF-16LE 再 base64,单串参数无引号地狱),如 `powershell -EncodedCommand <base64>`。
- **远端中文输出乱码**:PowerShell 默认输出编码非 UTF-8,中文路径/日志乱码难排查。对策:执行前先设 `[Console]::OutputEncoding = [Text.Encoding]::UTF8`。
- **Windows Server 2016 无 Add-WindowsCapability**:该 cmdlet 2019+ 才有,Server 2016 上先试 capability 必然失败。对策:OpenSSH 用 MSI 安装包(或按官方手动解包),不要纠结 capability。
- **uvicorn factory 必须显式 `--factory`**:自动检测在 Server 2016 上报 `create_factory() takes 0 args`(探测不认该形态)。对策:显式 `uvicorn "mod:create_app" --factory`。
- **schtasks 保活后台服务**:SSH 会话断开即杀子进程,远程服务必须用 schtasks 计划任务拉起保活。注意 schtasks 上下文**不继承 SSH 会话的环境变量** —— 环境变量经 bat 文件传递(`set VAR=...` 后启动命令写在 bat 里),不要依赖 SSH env。
- **改代码后必须 taskkill 全杀再重启**:Windows 端口被旧进程占用时新进程起不来,旧进程继续服务(改了像没改)。对策:改完代码 `taskkill /F /IM python.exe`(或按 PID 树)全杀 → 重启 → 验证端口监听。
- **PowerShell 5.1 无 BOM UTF-8 文件读乱码**:5.1 按 ANSI 读无 BOM UTF-8,脚本里中文注释/字符串乱码(有 BOM 才按 UTF-8)。对策:脚本注释用英文,或文件存带 BOM。
- **金蝶服务器 8000 端口被占用**:金蝶默认 WebSite 占用 8000,编译服务默认端口撞车。对策:编译服务换端口(如 8001),.env 的 `COMPILE_SERVICE_URL` 同步。
