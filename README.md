# functionCallTool

基于 LangChain/LangGraph 的多步骤任务 Agent 组。

## 目录结构

```
.
├── common/                  # 共享层:LLM 工厂、配置、prompt 加载
├── agents/
│   └── agent1/              # 第一个多步骤任务 Agent(LangGraph 循环图)
│       ├── agent.py         # 图构建
│       ├── prompts/         # 本 agent 的 prompt(system.md 默认)
│       └── utils/           # state / nodes / tools
├── tests/                   # pytest 测试
├── .env.example             # 环境变量模板(复制为 .env 填密钥)
├── requirements.txt
├── pyproject.toml
└── langgraph.json           # LangGraph 配置(agent 注册)
```

## 设计文档

- [Agent1 目录架构设计](docs/superpowers/specs/2026-08-06-agent1-langgraph-design.md)

## 快速开始(待实现后)

```bash
cp .env.example .env        # 填 DEEPSEEK_API_KEY
pip install -r requirements.txt
```
