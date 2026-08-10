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
| [api.py](api.py) | FastAPI:提交/进度/status/方案/勾选/stop/入库/导出 + health 共 9 接口 |
| [auth.py](auth.py) | apikey 鉴权 + 资源归属校验(越权 403) |
| [billing.py](billing.py) | 计费:创建记 pending,commit 转正式(1 单位),限并发 |
| [graph/state.py](graph/state.py) | 数据模型:SchemeGroup→Scheme→Track 三级 + AgentState |
| [graph/nodes.py](graph/nodes.py) | 6 步节点:每步内联 prompt + LLM(强制 JSON)+ 调 skill 脚本标准化 |
| [graph/flows.py](graph/flows.py) | 图构建:顺序边,单步失败标 error 不中断 |
| [tools/websearch.py](tools/websearch.py) | gateway MCP websearch 池(3 引擎自动切换,单例连接) |
| [skills/loader.py](skills/loader.py) | load_skill 工具(每步节点绑定,LLM 主动调方法论,2 回合上限) |
| [store/scheme_store.py](store/scheme_store.py) | JSON 文件库(草稿/正式/索引) |
| [store/converter.py](store/converter.py) | 勾选后的方案组 → skill spec 格式 → Excel |
| [deploy/](deploy/) | 生产部署套件:Dockerfile/精简依赖/compose/deploy.sh,用法见 [deploy/README.md](deploy/README.md) |
| [API.md](API.md) | 接口文档(全真实返回示例);对接方文档 [INTEGRATION.md](INTEGRATION.md) |

## 常用操作

- **改 6 步 prompt**:`graph/nodes.py` 的 `_STEP_PROMPTS`(内联,每步做什么 + 输出格式)。
- **改 6 步格式契约**:`skills/overseas-sentiment-query-builder/references/output-formats.md` + 对应 `scripts/stepN.py`(校验/标准化/GAP)。
- **加 skill**:复制到 `skills/`(agent 专属)或 `common/skills/`(共享),`loader.py` 的 `_AVAILABLE_SKILLS` 注册摘要。
- **改 skill 加载方式**:`graph/nodes.py` 的 `_SKILL_HINT`(工具提示)+ `bind_tools([load_skill], strict=True)`(每步绑定);上限 2 回合在 `_step_node` 的工具循环。
- **接真实搜索**:`tools/websearch.py` 已接 gateway MCP 池;改 `.env` 的 `MCP_GATEWAY_URL/TOKEN`。
- **配 apikey**:`.env` 的 `API_KEYS_JSON`(apikey→用户映射)。
- **跑测试**:`pytest tests/test_sentiment_query_agent.py`(脚本/store/鉴权/计费单测;图/端到端需外部服务)。
- **启动 API**:`uvicorn agents.sentiment_query_agent.api:app --reload`。

## 发布流程(生产 10.33.17.72)

详见 [deploy/README.md](deploy/README.md),设计文档 `docs/superpowers/specs/2026-08-10-sentiment-query-agent-prod-deploy-design.md`。

- **发布**:`bash agents/sentiment_query_agent/deploy/deploy.sh`(rsync 上机 → docker build → compose up → 健康检查)。
- **前置**:服务器 `/opt/sentiment-query-agent/.env` 已放置(手工,不进 git/rsync);缺失脚本会中止并提示。
- **端口**:API 8000,演示页 nginx 80;日志 `/home/logs/sentiment-query-agent/api.log`;数据 `/opt/sentiment-query-agent/data/`。
- **回滚**:`IMAGE_TAG=<旧tag>` 重启 compose;data 卷独立,不丢数据。
- **注意**:重启容器会终止运行中的流水线;发布前先用 status 接口确认无在跑任务。

## 约束

- LLM 经 `common/llm.py` 工厂,不直接 new。
- **uvicorn 必须单 worker**:sqlite checkpoint + JSON 文件库的并发模型限制(已加 WAL + index.json 双锁),横向扩需 PostgresSaver 改造。
- **load_skill 工具回合 2 必须换无工具 LLM**(nodes.py):deepseek-v4-flash 带工具绑定时工具回合后重复发 tool_calls 且 content 空(2026-08-10 生产事故,已修)。
- skill 分步脚本是格式契约唯一执行器,节点不手写格式化逻辑;脚本校验失败与 bad_json 共用重试预算(总上限 3 次)。
- 用户标识(apikey)只进日志/计费,不进 span label(OTel 高基数约束)。
- commit 后方案组冻结,改勾选须重新生成;仅 review 状态可 commit(stopped/generating 拒绝)。
- skill 原样保留在项目内(agent 专属),不依赖 ~/.claude/skills/。
