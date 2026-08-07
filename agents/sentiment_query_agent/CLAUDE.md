# sentiment-query-agent 开发指南

海外舆情检索方案生成 Agent:用户输入一个中文公司名,自动完成 6 步流水线(实体测绘→主体画像→关键词字典→双轨检索式→属地信源→频次定级),产出方案组 + 组内多方案,经 API 勾选确认后固化入库。

## 本 agent 是什么

- 职责:把"监测某公司海外舆情"的模糊需求,变成可勾选确认的检索方案组,固化入库、可导出 Excel。
- 开发前必读:根目录 [CLAUDE.md](../../CLAUDE.md) 和 [docs/dev-standards.md](../../docs/dev-standards.md)(必须依据 langchain MCP 文档/API 开发)。

## 架构

6 步流水线顺序图,每步「websearch → LLM 生成 → 调 skill 分步脚本按格式传回」:

```
START → step1 → step2 → step3 → step4 → step5 → step6 → END
 实体测绘  画像   关键词   检索式   信源   频次定级
```

| 文件 | 职责 |
|---|---|
| [agent.py](agent.py) | 图构建入口:`run_pipeline()` 跑完整流水线,AsyncSqliteSaver checkpointer(thread_id=group_id) |
| [api.py](api.py) | FastAPI:提交/进度/方案/勾选/入库/导出 6 接口 |
| [auth.py](auth.py) | apikey 鉴权 + 资源归属校验(越权 403) |
| [billing.py](billing.py) | 计费:创建记 pending,commit 转正式(1 单位),限并发 |
| [graph/state.py](graph/state.py) | 数据模型:SchemeGroup→Scheme→Track 三级 + AgentState |
| [graph/nodes.py](graph/nodes.py) | 6 步节点:每步内联 prompt + LLM(强制 JSON)+ 调 skill 脚本标准化 |
| [graph/flows.py](graph/flows.py) | 图构建:顺序边,单步失败标 error 不中断 |
| [tools/websearch.py](tools/websearch.py) | gateway MCP websearch 池(3 引擎自动切换,单例连接) |
| [skills/loader.py](skills/loader.py) | load_skill 工具(渐进式披露,agent→common 查找) |
| [store/scheme_store.py](store/scheme_store.py) | JSON 文件库(草稿/正式/索引) |
| [store/converter.py](store/converter.py) | 勾选后的方案组 → skill spec 格式 → Excel |

## 常用操作

- **改 6 步 prompt**:`graph/nodes.py` 的 `_STEP_PROMPTS`(内联,每步做什么 + 输出格式)。
- **改 6 步格式契约**:`skills/overseas-sentiment-query-builder/references/output-formats.md` + 对应 `scripts/stepN.py`(校验/标准化/GAP)。
- **加 skill**:复制到 `skills/`(agent 专属)或 `common/skills/`(共享),`loader.py` 的 `_AVAILABLE_SKILLS` 注册摘要。
- **接真实搜索**:`tools/websearch.py` 已接 gateway MCP 池;改 `.env` 的 `MCP_GATEWAY_URL/TOKEN`。
- **配 apikey**:`.env` 的 `API_KEYS_JSON`(apikey→用户映射)。
- **跑测试**:`pytest tests/test_sentiment-query-agent.py`(脚本/store/鉴权/计费单测;图/端到端需外部服务)。
- **启动 API**:`uvicorn agents.sentiment-query-agent.api:app --reload`。

## 约束

- LLM 经 `common/llm.py` 工厂,不直接 new。
- skill 分步脚本是格式契约唯一执行器,节点不手写格式化逻辑。
- 用户标识(apikey)只进日志/计费,不进 span label(OTel 高基数约束)。
- commit 后方案组冻结,改勾选须重新生成。
- skill 原样保留在项目内(agent 专属),不依赖 ~/.claude/skills/。
