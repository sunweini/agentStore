# 版本更新说明(CHANGELOG)

项目:agentStore — 基于 LangChain/LangGraph 的多步骤任务 Agent 组
仓库:https://github.com/sunweini/agentStore

---

## Agent 索引

| Agent | 当前版本 | CHANGELOG |
|---|---|---|
| sentiment-query-agent | v1.24.0 | [CHANGELOG](agents/sentiment_query_agent/CHANGELOG.md) |
| kingdee-plugin-agent | v1.26.0 | [CHANGELOG](agents/kingdee_plugin_agent/CHANGELOG.md) |

> 每 agent 独立版本号序列,撞号消除。改动归属哪个 agent → 更新该 agent 的
> CHANGELOG + bump 该 agent 版本号;纯项目级 → 本文件「项目级变更」区。

## 项目级变更

跨 agent / 公共层变更记这里:
- common/ 公共库(config/llm/rag/otel/db 等)
- compile_service(kingdee 用但属公共基建)
- 依赖升级 / 工作流约定 / 基建

## 项目级历史

### v0.1.0 — 2026-08-06(项目初始化)

(自 v0.1.0 后,agent 功能版本全归各 agent CHANGELOG,根文件仅记项目级。)
