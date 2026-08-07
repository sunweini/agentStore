# Sentiment-Query-Agent 目录架构设计(LangGraph)

日期:2026-08-06
状态:已批准

## 1. 背景与目标

- 项目准备开发一组基于 LangChain 的多步骤任务 Agent,先做第一个。
- 开发全程参考 LangChain/LangGraph 官方文档与 API(经 MCP 查询)。
- 第一个 agent 先搭通用骨架,跑通「LLM → 工具调用 → 回传 → 再决策」闭环,后续填具体业务任务。

## 2. 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 语言 | Python | LangChain 生态最成熟 |
| 编排框架 | LangGraph | 官方推荐,显式状态、循环/条件分支、多 agent 扩展 |
| LLM | DeepSeek(经 ChatOpenAI,OpenAI 兼容 API) | 需求指定;ChatOpenAI 完全兼容 Chat Completions API |
| 依赖管理 | requirements.txt + pyproject.toml | LangGraph 官方支持,部署用 |

## 3. 目录结构

```
functionCallTool/
├── common/                            # 共享层:所有 agent 复用
│   ├── __init__.py
│   ├── llm.py                         # 多供应商模型工厂
│   ├── config.py                      # 配置加载 (.env / 环境变量)
│   ├── prompts.py                     # prompt 加载工具
│   └── base.py                        # 公共基类/工具函数
├── agents/                            # 各 agent 平级目录
│   └── sentiment-query-agent/                        # 第一个多步骤任务 Agent
│       ├── __init__.py
│       ├── agent.py                   # 图构建 (StateGraph)
│       ├── prompts/                   # 本 agent 的 prompt
│       │   └── system.md              # 默认系统提示词(一个就够)
│       └── utils/
│           ├── __init__.py
│           ├── state.py               # 状态定义
│           ├── nodes.py               # 节点函数 (LLM/路由)
│           └── tools.py               # 本 agent 专属工具
├── tests/                             # pytest 测试
│   ├── __init__.py
│   └── test_sentiment-query-agent.py
├── .env                               # 环境变量 (gitignore)
├── .env.example                       # 环境变量模板 (提交)
├── .gitignore
├── requirements.txt                   # 依赖
├── pyproject.toml                     # 项目元数据/工具配置
└── langgraph.json                     # LangGraph 配置 (agent 注册)
```

- `agents/sentiment-query-agent/` 严格对照 LangGraph 官方 application-structure(agent.py + utils/{state,nodes,tools}.py)。
- `common/` 为多 agent 扩展:共享 LLM 工厂、配置、prompt 加载。
- 后续 agent 平级加目录 + langgraph.json 注册一行。

## 4. 多供应商模型工厂

`common/llm.py` 提供供应商注册表,换供应商不改代码:

```python
def get_chat_model(provider: str | None = None, model_id: str | None = None) -> BaseChatModel:
    """供应商驱动模型工厂。provider 缺省用 LLM_PROVIDER,model 缺省用对应供应商默认模型。"""
```

`.env` 约定(每个供应商一组):

```env
LLM_PROVIDER=deepseek          # 当前默认供应商
LLM_MODEL=deepseek-chat        # 当前默认模型
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-4o
ANTHROPIC_API_KEY=sk-ant-xxx
ANTHROPIC_MODEL=claude-sonnet-5
```

- 内部注册表 `{provider: builder}`;DeepSeek 用 `ChatOpenAI(base_url=...)` 接。
- 后续加供应商 = 注册表加一项(如 Claude 用 `ChatAnthropic`,装 langchain-anthropic)。

## 5. Prompt 管理

- **能力**:prompt 分离为可选能力,非强制。
- 默认一个 agent 一个 `system.md`;复杂 agent 可加 `planner.md`、`executor.md` 等,node 各用各的。
- 存放:agent 专属 prompt 放 `agents/<agent>/prompts/`;共享 prompt 以后放 `common/prompts/`。
- 加载:`common/prompts.py` 的 `load_prompt(agent, name="system")` 返回 ChatPromptTemplate,默认加载 system.md,传其他 name 加载对应文件。
- prompt 用 .md 文件存,不混在代码里,方便单独调整/测试。

## 6. 数据流(图结构)

循环型 LangGraph(ReAct 风格):

```
START → agent_node ──有 tool_calls──→ tools_node
              │                          │
              └──────无 tool_calls───────┘
                     → END
```

- **agent_node**:加载 prompt,把 `state["messages"]` 发给 LLM。
- **有 tool_calls** → `tools_node`(官方 prebuilt ToolNode)执行工具,结果作为 ToolMessage 回 `state["messages"]`,回到 agent_node。
- **无 tool_calls** → END,输出最终回答。

状态(`utils/state.py`):

```python
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # 自动追加
    task: str
    result: str
```

路由(`utils/nodes.py`):官方标准模式——最后一条消息有 tool_calls 走 tools,否则 END。

图构建(`agent.py`):StateGraph + ToolNode + conditional_edges,`graph.compile()` 返回,`langgraph.json` 注册。

## 7. 工具层

- 用 LangChain `@tool` 装饰器定义,放 `agents/sentiment-query-agent/utils/tools.py`。
- 第一版放 2 个占位工具(模拟查库存/算价格),验证工具调用链路;后续替换真实 API 只改工具函数内部,图结构不动。
- 工具列表在 `agent.py` 构建图时注入,工具增减不影响图结构。

## 8. 错误处理

1. LLM 调用失败:node 内 catch,错误转消息回图,不中断整个流程。
2. 工具执行失败:ToolNode 默认把异常转 ToolMessage 返回 LLM,LLM 判断重试或换策略。
3. 循环上限:`recursion_limit`,防死循环,超限抛错终止。
4. 日志:遵循可观测性规范,node 进出/工具调用/错误打结构化 key=value 日志。

## 9. 依赖管理

requirements.txt(核心):

```
langchain>=0.3
langchain-core
langgraph>=0.2
langchain-openai        # DeepSeek 经 ChatOpenAI
langchain-anthropic     # 预留
python-dotenv
```

- `.env` 真实密钥,gitignore;`.env.example` 模板,提交。
- 密钥不硬编码,`common/config.py` 统一读。

## 10. 测试

`tests/test_sentiment-query-agent.py`,三层:
1. 工具单测:直接调工具函数,断言返回。
2. 图单测:mock LLM,断言图按预期走节点/路由。
3. 端到端:真实调 DeepSeek(有 key 时),跑通完整流程。

## 11. langgraph.json

```json
{
  "dependencies": ["langchain-openai", "./common", "./agents"],
  "graphs": {
    "sentiment-query-agent": "./agents/sentiment-query-agent/agent.py:build_agent"
  },
  "env": "./.env"
}
```

后续 agent2 注册 = graphs 加一行。

## 12. 范围外(后续迭代)

- 真实业务工具(金蝶等)
- agent 间协作(多 agent 编排)
- 持久化/checkpoint
- 部署(LangSmith Deployment)
