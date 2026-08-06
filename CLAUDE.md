# functionCallTool 项目指南

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

## 架构约定

- **编排**:LangGraph 循环图(agent → tools → agent → END),不用 AgentExecutor。`recursion_limit` 是运行时 config 参数(`graph.invoke(inputs, config={"recursion_limit": N})`),不是 compile 参数。
- **多供应商模型**:`common/llm.py` 供应商注册表。换供应商改 `.env` 的 `LLM_PROVIDER`,加供应商注册表加一项,代码不动。
- **prompt 分离是能力非强制**:`common/prompts.py` 的 `load_prompt(agent, name="system")` 加载 `agents/<agent>/prompts/<name>.md`。默认一个 system.md,复杂 agent 按 node 拆多 prompt。
- **目录**:agent 平级放 `agents/`(每个含 agent.py + utils/{state,nodes,tools}.py + prompts/),共享层在 `common/`,新 agent 在 `langgraph.json` 注册。
- **每个 agent 必须有独立 CLAUDE.md**:`agents/<agent>/CLAUDE.md`,写清本 agent 的职责、架构、常用操作(加工具/改提示词/接真实业务)、约束。在 agent 目录工作时自动加载。

## 项目状态

- agent1 已重构为**海外舆情检索方案生成 Agent**:输入中文公司名 → 6 步流水线(skill 分步脚本按格式传回)→ 方案组 → API 勾选确认 → JSON 入库。10 测试全过。
- 设计文档:`docs/superpowers/specs/2026-08-06-agent1-sentiment-query-agent-design.md`
- 后续:接真实业务工具(如金蝶)、交互式试跑(需 MCP gateway + DeepSeek key)、真实监控主体验证。

## 技术栈

Python + LangChain/LangGraph + DeepSeek(经 ChatOpenAI,OpenAI 兼容 API)。依赖见 `requirements.txt`,环境变量见 `.env.example`。
