# agentStore 项目指南

基于 LangChain/LangGraph 的多步骤任务 Agent 组项目。

## 开发铁律

- **必须参考 LangChain 官方文档和 API 指引开发**(用户的硬性要求,详见 [docs/dev-standards.md](docs/dev-standards.md))。开发任何 agent 功能前,先用 langchain MCP 查文档确认 API 用法,禁止凭记忆写 API。
  - 文档:docs-langchain MCP(`search_docs_by_lang_chain` / `query_docs_filesystem`)
  - API 参考:reference-langchain MCP(`search_api` / `get_symbol`)
- **开发前必读 [docs/dev-standards.md §7 通用开发经验](docs/dev-standards.md#7-通用开发经验踩坑记录开发-agent-前必读)**:LangGraph checkpointer 用法、LLM JSON Mode、ChatPromptTemplate 转义、异步 LLM、skill 分步脚本等踩坑记录,开发时对照避免重踩。

## 开发流程(用户偏好)

1. **先设计后实现**:新 agent/新功能先讨论设计,用户确认后才动手。设计文档存档 `docs/superpowers/specs/`。
2. **骨架阶段只建目录+文档,不写实现代码**:架构确定后先创建目录结构和占位文档(docstring 写明职责/待实现/设计文档引用),用户说"继续"才写实现。
3. 实现完成后跑测试验证(`pytest`),通过后 commit。
4. **每次开发收尾必须更新 CHANGELOG**(详见 dev-standards §4):改动归属哪个
   agent → 更新该 agent 的 `agents/<agent>/CHANGELOG.md` 并 bump 该 agent 版本号
   (当前最大号 +1);纯项目级(common/compile_service/依赖)→ 根 `CHANGELOG.md`
   项目级区。测试通过后 commit 推送。

## 架构约定

- **编排**:LangGraph 循环图(agent → tools → agent → END),不用 AgentExecutor。`recursion_limit` 是运行时 config 参数(`graph.invoke(inputs, config={"recursion_limit": N})`),不是 compile 参数。
- **多供应商模型**:`common/llm.py` 供应商注册表。换供应商改 `.env` 的 `LLM_PROVIDER`,加供应商注册表加一项,代码不动。
- **prompt 分离是能力非强制**:`common/prompts.py` 的 `load_prompt(agent, name="system")` 加载 `agents/<agent>/prompts/<name>.md`。默认一个 system.md,复杂 agent 按 node 拆多 prompt。
- **目录**:agent 平级放 `agents/`(每个含 agent.py + utils/{state,nodes,tools}.py + prompts/),共享层在 `common/`,新 agent 在 `langgraph.json` 注册。
- **skill 方法论供给**:每步节点绑定 `load_skill` 工具(`bind_tools(strict=True)`,最多 2 回合),LLM 需要专业指导时主动调用;运行时脚本调用放代码(方案 A),SKILL.md 文档写清脚本用法(知识层)。详见 dev-standards §7.3。
- **每个 agent 必须有独立 CLAUDE.md**:`agents/<agent>/CLAUDE.md`,写清本 agent 的职责、架构、常用操作(加工具/改提示词/接真实业务)、约束。在 agent 目录工作时自动加载。
- **部署命名规范(必守)**:docker compose 项目名 = `deploy-<agent>`(如 contract 为 `deploy-contract-review-agent`),**禁止**用 "deploy" 等通用名 —— 同机 sentiment / mcp-gateway 共用 "deploy" 项目,容器名会冲突(曾发生 deploy-api-1 被 contract 镜像覆盖、线上 sentiment 8000 中断事故)。端口按 agent 隔离(sentiment 8000,contract 8000/测试 8001)。

## 项目状态

- sentiment-query-agent 已交付为**海外舆情检索方案生成 Agent**:输入中文公司名 → 6 步流水线(skill 分步脚本按格式传回)→ 方案组 → API 勾选确认 → JSON 入库 + 计费。**已部署生产 10.33.17.72**(Docker Compose,API:8000/nginx:80),9 接口(含 stop/status)全链路生产实测通过,272 测试全绿。
- **多用户配额与资费(v1.24.0,feature/quota-billing-stats 分支,未部署)**:apikey 即用户,免费/付费额度,apikey 管理(创建/修改/删除),管理员,8 新接口,MySQL 存储(agentstore 库)。设计:`docs/superpowers/specs/2026-08-11-quota-billing-stats-design.md`。
- 前端演示页 `web/demo.html`(6 步实时回显 + 勾选/入库/导出)已跑通全流程。
- sentiment 文档:接口文档 `agents/sentiment_query_agent/API.md`(全真实返回示例)、AI 对接规范 `INTEGRATION.md`、发布流程 `agents/sentiment_query_agent/deploy/README.md`、部署设计 `docs/superpowers/specs/2026-08-10-sentiment-query-agent-prod-deploy-design.md`。
- 设计文档:`docs/superpowers/specs/2026-08-06-sentiment-query-agent-sentiment-query-agent-design.md`
- 后续:真实监控主体验证、prompt 调优(频次定级偏保守)、新 agent、接爬虫调度器。

## 技术栈

Python + LangChain/LangGraph + DeepSeek(经 ChatOpenAI,OpenAI 兼容 API)。依赖见 `requirements.txt`,环境变量见 `.env.example`。
