# agent1 开发指南

通用多步骤任务 Agent(LangGraph 循环图),基于 LangChain/LangGraph 开发。

## 本 agent 是什么

- 职责:拆解任务 → 调用工具 → 汇总结果(当前为通用骨架,工具是 mock 占位)
- 开发前必读:根目录 [CLAUDE.md](../../CLAUDE.md) 和 [docs/dev-standards.md](../../docs/dev-standards.md)(必须依据 langchain MCP 文档/API 开发)

## 架构

```
START → agent_node ──有 tool_calls──→ tools_node
              │                          │
              └──────无 tool_calls───────┘
                     → END
```

| 文件 | 职责 |
|---|---|
| [agent.py](agent.py) | 图构建:`build_agent()` 返回编译图。`langgraph.json` 注册入口 `agent1:build_agent` |
| [utils/state.py](utils/state.py) | `AgentState`:`messages`(add_messages 自动追加)/ `task` / `result` |
| [utils/nodes.py](utils/nodes.py) | `agent_node`(LLM 决策,加载 prompt、绑工具、发消息)、`should_continue`(官方标准路由) |
| [utils/tools.py](utils/tools.py) | 工具定义 + `TOOLS` 列表(图绑定的工具清单) |
| [prompts/system.md](prompts/system.md) | 系统提示词,`common/prompts.py` 的 `load_prompt("agent1")` 加载 |

## 常用操作

- **加工具**:`utils/tools.py` 加 `@tool` 函数 → 加入 `TOOLS` 列表。图结构/节点不动。
- **改提示词**:改 `prompts/system.md`。要按 node 拆多 prompt 时,在 `prompts/` 加 `xxx.md`,`load_prompt("agent1", "xxx")` 加载。
- **接真实业务**:替换工具函数内部实现(mock → 真实 API),签名和图结构不动。
- **跑测试**:`pytest tests/test_agent1.py`(端到端需 `.env` 配 `DEEPSEEK_API_KEY`,无 key 自动跳过)。

## 约束

- LLM 经 `common/llm.py` 工厂获取(DeepSeek 默认,经 ChatOpenAI),不直接 new 模型。
- prompt 从文件加载,不硬编码在代码里。
- 日志结构化(key=value),遵循可观测性规范。
