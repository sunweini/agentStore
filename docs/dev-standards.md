# AI Agent 开发规范(基于 LangChain)

版本:1.0
适用范围:本项目中**所有** agent 的开发(现有 agent1 及后续 agent2、agent3…… 及任何 agent 功能)。

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

## 5. 质量要求

- 测试覆盖工具调用链路(agent→tools→agent→END)。
- 端到端测试无 key 自动跳过,不阻塞本地无 key 环境。
- 密钥不硬编码,统一 `.env` + `common/config.py`。
- 日志结构化(key=value),遵循可观测性规范。

## 6. Agent CLAUDE.md 模板

**新建 agent 时,必须按此模板生成 `agents/<agent>/CLAUDE.md`**,替换 `<agent>` 占位符,并按实际架构填写。参考实例:`agents/agent1/CLAUDE.md`。

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
