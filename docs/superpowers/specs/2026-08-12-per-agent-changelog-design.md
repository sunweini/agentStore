# 每 agent 独立 CHANGELOG + 版本号管理设计

日期:2026-08-12
状态:已确认(用户批准:每 agent 独立版本号 / 根记项目级+索引 / 历史完整迁移)

## 背景与问题

当前单根 `CHANGELOG.md` 混记 2 个 agent(sentiment-query-agent + kingdee-plugin-agent)的版本历史,共用全局 v1.x 序列:

- **撞号**:两 agent 都曾在 v1.21.0 加版本段(sentiment 生产部署 vs kingdee 持久化),靠手工重编号(v1.25/1.26)规避,后续还会再撞。
- **不可追溯**:改动归属不清晰,一个 agent 的版本段混在另一个的序列里,版本号无法反映某个 agent 的真实迭代轨迹。
- **维护割裂**:每 agent 有独立 CLAUDE.md,但 CHANGELOG 无 agent 边界,收尾时不知道往哪写。

## 目标

1. 每 agent 独立 CHANGELOG 文件 + 独立版本号序列,撞号彻底消除。
2. 历史版本段完整迁移(不丢可追溯性),版本号原样保留。
3. 根 CHANGELOG 降为「项目级事项 + 索引」。
4. 开发收尾流程明确:改动归 agent 就写该 agent 的 CHANGELOG + bump 该 agent 版本号。

## 文件结构

```
CHANGELOG.md                          # 根:项目级事项 + agent 索引(见下)
agents/sentiment_query_agent/CHANGELOG.md   # sentiment 版本历史
agents/kingdee_plugin_agent/CHANGELOG.md    # kingdee 版本历史
```

## 版本号规则

- **每 agent 独立续号,互不共享**:sentiment 与 kingdee 各自 v1.x 序列。
- 迁移后各 agent 当前版本(取其历史段中最大号):
  - kingdee-plugin-agent:`v1.26.0`(持久化 + 终审修复,置顶段)→ 下版 v1.27
  - sentiment-query-agent:`v1.24.0`(多用户配额,已部署生产)→ 下版 v1.25
- 撞号消除:两 agent 各自序列,永不复用同一版本号语义。
- **历史缺口允许**:迁移后各序列可能有号缺口(全局共用期的真实历史,如 kingdee 缺 v1.21~1.24),不补齐、不重编;新版本一律 `当前最大号 + 1`。

## 历史迁移(34 段完整迁移,版本号原样保留)

**精确归属表**(逐段核对,共 34 段):

### kingdee-plugin-agent(24 段)
| 版本 | 标题(节选) | 归属依据 |
|---|---|---|
| v1.26.0 / v1.25.0 | 终审修复 / 任务持久化 | 显式标 agent |
| v1.20.0 ~ v1.9.0 | skill 评估 / 环境类升级 / Windows 编译 / RAG / E2E 等 | 显式标 agent(12 段) |
| v1.8.1 | kingdee 三份文档 | 显式标 agent |
| v1.8.0 | w2 设计经验库回流 | w2 worker 是 kingdee 概念 |
| v1.7.1 / v1.7.0 | seed_load / knowledge-steward | kingdee 知识库 skill |
| v1.6.1 / v1.6.0 | errors.md 方法论 / worker 方法论 skill | kingdee skill 体系 |
| v1.5.0 | load_skill 机制(「对照 sentiment 模式」) | 标题即指 kingdee 复刻 sentiment 的 load_skill |
| v1.4.0 / v1.4.1 / v1.3.0 | 全流程交付 / Plan C 终审 / 主管图 | 显式标 agent / Plan A/B/C 属 kingdee |

### sentiment-query-agent(9 段)
| 版本 | 标题(节选) | 归属依据 |
|---|---|---|
| v1.24.0 | 多用户配额管理 + 资费统计(已部署生产) | 功能主体是 sentiment 计费(billing/apikey/生产部署);段内含 common/db.py 公共基建,归 sentiment |
| v1.23.0 / v1.22.0 / v1.21.0 | 格式校验重试 / v4-flash 工具循环 / 生产部署 | 显式标 agent |
| v1.2.0 | 轨 key 语义化 + 移除风险等级 | 「轨 key」是 sentiment 方案组概念 |
| v1.2.0 | 生产三错修复 + 推理模型调优 | deepseek-v4-flash 调优是 sentiment |
| v1.1.0 | load_skill 方法论接入 | load_skill 最早在 sentiment 落地(kingdee 1.5.0 明确「对照 sentiment 模式」) |
| v1.0.0 | sentiment 正式交付 | 显式标 agent |
| v0.2.0 | agent1 重构为舆情方案生成 Agent | sentiment 前身(agent1) |

### 根(项目级,1 段)
| 版本 | 标题 | 说明 |
|---|---|---|
| v0.1.0 | 项目初始化 | 项目级,留根文件 |

**合计:24 + 9 + 1 = 34 段 ✅(与原根文件段数一致,无丢失)**

迁移规则:
- 每 agent 的 CHANGELOG 按版本号**降序**排列(最新置顶)。
- 段内版本号**原样保留**,不重编(含 sentiment 序列里两个 v1.2.0 —— 轨 key 语义化 与 生产三错,原历史重复号,标题不同可区分,保留为历史事实)。
- **版本号缺口说明**:kingdee 序列 v1.20 后直接 v1.25(因全局历史里 v1.21~1.24 被 sentiment 占用)—— 允许历史缺口(全局共用造成),新版本从当前最大号 +1 续。
- 迁移后根文件删去全部 34 段,重建为索引 + 项目级区(仅保留 v0.1.0 项目初始化段)。
- 独立文件段标题**去掉 agent 前缀**(独立文件无需):`## v1.26.0 — 2026-08-10(终审修复 ...)`。

### 其余文件与目录

- `agents/data/billing/` 是 sentiment 计费数据产物,随 sentiment 版本,不单独管。

## 根 CHANGELOG = 索引 + 项目级

```markdown
# 版本更新说明(CHANGELOG)

项目:agentStore — 基于 LangChain/LangGraph 的多步骤任务 Agent 组

## Agent 索引

| Agent | 当前版本 | CHANGELOG |
|---|---|---|
| sentiment-query-agent | v1.24.0 | [CHANGELOG](agents/sentiment_query_agent/CHANGELOG.md) |
| kingdee-plugin-agent | v1.26.0 | [CHANGELOG](agents/kingdee_plugin_agent/CHANGELOG.md) |

## 项目级变更

跨 agent / 公共层变更记这里:
- common/ 公共库(config/llm/rag/otel/db 等)
- compile_service(kingdee 用但属公共基建)
- 依赖升级 / 工作流约定 / 基建

## 项目级历史

### v0.1.0 — 2026-08-06(项目初始化)

(自 v0.1.0 后,agent 功能版本全归各 agent CHANGELOG,根文件仅记项目级。)
```

## 开发流程收尾约定

每 agent 的 `CLAUDE.md` 「常用操作」区加一条:

- **收尾更新 CHANGELOG**:改动归属本 agent → 写本 agent 的 `agents/<agent>/CHANGELOG.md`,bump 本 agent 版本号(当前最大号 +1);纯项目级(common/compile_service/依赖)→ 根 `CHANGELOG.md` 项目级区。

根 `CLAUDE.md` 开发流程第 4 条同步改为上述规则。

## 验证

- 迁移后根文件版本段数 = 1(仅 v0.1.0 项目初始化,其余为索引 + 项目级区)。
- kingdee CHANGELOG = 24 段,sentiment CHANGELOG = 9 段,合计 + 根 1 段 = 34(原根文件全部段,无丢失)。
- 每 agent 文件内版本号降序;重复号仅保留原历史已重复的(sentiment 两个 v1.2.0),无新增重复。
- 撞号场景:两 agent 各从 v1.27 / v1.25 续,无冲突。
- 迁移用脚本核对:迁移前后 `grep -c "^## v"` 各文件段数之和恒等(34)。

## 影响

- 纯文档/结构改动,不动代码。
- 每 agent 的 CLAUDE.md + 根 CLAUDE.md 各改一处(收尾约定)。
- 历史段原样搬运,不重写内容(版本号/标题仅按归属迁移,去掉 agent 前缀,其余逐字保留)。
