# 版本更新说明(CHANGELOG)

项目:agentStore — 基于 LangChain/LangGraph 的多步骤任务 Agent 组
仓库:https://github.com/sunweini/agentStore

---

## v1.1.0 — 2026-08-07(load_skill 方法论接入)

### 新增功能

- **load_skill 工具接入(方案 2a)**:每步节点绑定 load_skill 工具,LLM 需要方法论时主动调用(六层词表/双轨语法/信源/频次规则),拿到专业指导后按格式输出。最多 2 回合(回合 1 并行调工具 → 回合 2 生成 JSON),防死循环。
- **SKILL.md 补全**:工作流每步补「完成后调用脚本」指令(脚本调用约定/步骤对应/数据流/格式契约),skill 成为自包含知识包;标注方案 A(代码调用脚本)+ load_skill 方法论供给。

### 质量提升(load_skill 前后对比)

- 风险分级更有区分度:厄瓜多尔 c 轨 high、刚果金 b/c 轨 high(之前普遍 low/medium 偏保守)
- 快讯轨普遍正确配置(快讯/小时级)
- 方案名更具体:米拉多铜矿/迪兹瓦微电网/蒙古 ETT 选煤厂(之前泛"项目群")
- 识别地区更全:新增蒙古

### 技术要点

- DeepSeek JSON Mode + tool calling 兼容:实测 `bind_tools([...], strict=True)` + `response_format={"type":"json_object"}` 可同用,LLM 正确发 tool_calls 且后续输出纯 JSON
- 多轮工具调用:回合 1 tool_calls → 执行 load_skill → 喂 ToolMessage → 回合 2 生成 JSON

---

## v1.0.0 — 2026-08-07(sentiment-query-agent 正式交付)

首个完整交付版本:海外舆情检索方案生成 Agent。

### 新增功能

- **六步流水线**:输入中文公司名,自动完成实体测绘 → 主体画像 → 关键词字典 → 双轨检索式 → 属地信源 → 频次定级,每步产物实时可见
- **方案组生成**:输出方案组 + 组内多方案 × 多轨(a 全量 / b 精准 / c 不点名 / 快讯 / 司法 / 招标),含频次/风险等级/GAP 数据缺口标注
- **API 服务**(7 接口):提交任务 / 查进度 / 获取方案组 / 提交勾选 / 确认入库 / 导出 Excel / 健康检查
- **勾选确认机制**:方案级 + 轨级两级勾选,确认入库后冻结,可导出三 sheet Excel(检索任务清单 / 关键词字典 / 调度说明)
- **鉴权与计费**:apikey 鉴权 + 资源归属校验(越权 403)+ 每次完整生成计费 1 单位(并发安全)
- **前端演示页**:`web/demo.html` 六步实时回显 + 勾选入库导出全流程
- **领导汇报技术说明书**:`web/tech-doc.html`(为什么开发 Agent / 技术实现 / 演示方式)

### 技术要点

- LangGraph 状态机编排 6 步流水线,AsyncSqliteSaver 持久化(中断续跑)
- DeepSeek JSON Mode 强制结构化输出 + skill 分步脚本格式契约校验(缺字段自动记 GAP)
- gateway MCP websearch 池(brave / tavily / serpapi 三引擎,失败自动切换)
- OpenTelemetry 全链路可观测(OTLP exporter)
- 内网可访问(服务绑定 0.0.0.0)

### 修复

- 流水线端到端跑通系列:LLM 输出非 JSON / 模板花括号转义 / 同步调用阻塞事件循环 / 路径层级错误 / step4 轨 key 误判 / step6 索引越界
- 计费并发竞态:线程锁 + fcntl 文件锁,并发提交不丢记录

### 文档

- `docs/api.md` 接口文档(7 接口 + 错误码)
- `docs/deployment.md` 部署文档(配置/启动/运维/常见问题)
- `docs/dev-standards.md` 开发规范(§7 通用开发经验 15+ 条踩坑记录)

### 变更

- agent1 更名为 sentiment-query-agent(业务名,展示层连字符;包名 sentiment_query_agent 下划线)

---

## v0.2.0 — 2026-08-06(agent1 重构为舆情方案生成 Agent)

### 新增

- agent1 从通用骨架重构为海外舆情检索方案生成 Agent(设计文档评审 2 轮)
- skill 分步脚本模式:6 个 stepN.py 作为格式契约执行器(校验/标准化/记 GAP)
- skill 原生加载(渐进式披露 load_skill)+ 项目内 skill 目录策略(agent 专属 / common 共享)
- 6 步输出格式契约(§5.1,字段对齐 skill 最终 spec,导出零转换)
- auth/billing 拆分:apikey 鉴权 + 归属校验,计费 pending → committed(防刷限并发)

### 修复

- 设计审核修正:资源归属校验 / 计费冻结语义 / 导出转换层 / skill 分步加载 / OTel 高基数约束 / MCP 连接生命周期

---

## v0.1.0 — 2026-08-06(项目初始化)

### 新增

- 项目骨架:common 共享层(LLM 工厂 / 配置 / prompt 加载)+ agents/agent1 通用骨架(占位文档)
- 多供应商模型工厂:供应商注册表,换供应商改 `.env` 不改代码
- CLAUDE.md 项目指南 + 开发规范(必须依据 langchain MCP 文档/API 开发)
- 每个 agent 独立 CLAUDE.md 约定(§6 模板)

### 文档

- 设计文档:agent1 目录架构设计(LangGraph)
- 开发规范初版:开发铁律 / 架构约定 / 开发流程 / CLAUDE.md 模板
