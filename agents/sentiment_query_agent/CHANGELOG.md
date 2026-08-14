# sentiment-query-agent 版本更新说明(CHANGELOG)

> 版本号独立管理(每 agent 独立序列),历史从根 CHANGELOG 迁移(2026-08-12)。
> 收尾规则:改动归本 agent → 更新本文件 + bump 版本号(当前最大号 +1)。

---

## v1.25.0 — 2026-08-14(计费切公共组件,agent='sentiment';失败路径补 cancel_pending)

### 技术变更

- **计费/鉴权/apikey 管理切公共组件**:api.py 的 billing/auth/apikey_mgmt 全部改指
  `common/billing.py` / `common/auth.py` / `common/apikey_mgmt.py`,agent 固定
  'sentiment';统一表 agent_api_keys / agent_billing_records((apikey, agent) 复合主键,
  与 contract 同表同 schema,额度按 agent 维度隔离);bill_no = sentiment 的 group_id。
  本 agent 独立 billing.py / auth.py / apikey_mgmt.py 已删除(grep 无残留)。
- **接口端点/参数/返回零变更**:INTEGRATION 对接方不受影响;测试页 web/demo.html 全流程兼容。
- **失败路径补 cancel_pending**:runner 异常分支(原只落 failed 草稿)先
  `billing.cancel_pending` 释放并发额度,避免 pending 槽位泄漏(与 contract 一致)。
- **create_apikey 兼容包装 `_create_apikey_compat`**:公共 `create_apikey(agent, name, role)`
  为服务端随机 key,与 sentiment 现有"调用方传 key"语义不兼容;api.py 层校验 sk- 格式后
  手动插 agent_api_keys(agent='sentiment'),返回结构同旧版(apikey/free_quota=10/paid_quota=0),
  重复 key 409。
- **行为变化(接受)**:删除 apikey 走公共 deactivate_apikey(admin 目标可停用,contract 契约);
  update_apikey 不再做旧文件方案迁移(该迁移已确认不做);list_pending 返回 bill_no → api.py
  映射回 group_id 保持接口字段。
- **init_tables.sql**:新增 agent_api_keys / agent_billing_records 两表(结构同
  common/db.py init_tables);老表 api_keys / billing_records 保留不删(回滚路径)。

### 修复

- 测试 `tests/test_sentiment_query_agent.py` 计费/鉴权用例改指 common + agent='sentiment'、
  agent_* 表,断言语义保留(含失败路径 cancel 释放额度、先免费后付费、不足拒绝等)。

---

## v1.24.0 — 2026-08-12(多用户配额管理 + 资费统计,**已部署生产 10.33.17.72**)

> 发布:2026-08-12 全链路测试通过(配额流程 12 项验证)。生产 MySQL(agentstore 库)建表 + 数据迁移(6 方案组 owner 用户标识→apikey)+ 管理员(sk-demo-hefangyuan20260810,额度 99999999)初始化。

### 新增功能

- **配额体系**:用户即 apikey。每个 apikey 免费额度(初始 10)+ 付费额度(充值 0),commit 扣减(先免费后付费);提交时校验额度>0,不足 403
- **apikey 管理**(仅管理员):创建(默认免费 10/付费 0)/ 修改(换 key 资费继承 + 历史任务迁移)/ 删除(软删,数据保留)
- **管理员**:ADMIN_APIKEY(sk-demo-hefangyuan20260810),额度 99999999,不受权限控制,可查全部用户额度、可增减免费/付费额度
- **资费接口**:
  - 普通用户:查自己免费/付费额度总数、已用、剩余 + pending 数
  - 管理员:查所有普通用户 apikey 额度(按 apikey 分类)+ 汇总
- **pending 查询**:按 apikey 查当前 pending 任务
- **8 个新接口**:POST/PUT/DELETE /api/v1/apikeys、GET /api/v1/apikeys/list、GET /api/v1/apikeys/pending、GET /api/v1/billing/usage、POST /api/v1/billing/quota/paid、POST /api/v1/billing/quota/free

### 技术变更

- **存储迁移**:计费 JSON 文件 → MySQL(agentstore 库,api_keys + billing_records 两表)
- **鉴权改造**:API_KEYS_JSON 废弃,apikey 存 MySQL;owner = apikey 本身;管理员放行 assert_owner
- **数据库抽象**:common/db.py 双后端(MySQL 生产 / SQLite 测试),事务 + 占位符适配
- **数据迁移脚本**:migrate_legacy.py(JSON 计费 → MySQL、方案组 owner 迁移、api_keys 初始化,支持 dry-run/--apply)

### 部署修复(2026-08-12 发布)

- **pymysql 依赖缺失**:生产 requirements-agent.txt 补 `pymysql>=1.1,<2`(配额 MySQL 驱动)
- **容器跨网络**:api 容器(deploy_default)连不上 MySQL(在 deploy_mcp-net),compose 加 `deploy_mcp-net` 外部网络
- **迁移脚本路径**:容器内根探测(本地/容器兼容)+ DATA_DIR 环境变量(容器 /app/data)+ `--apply` 触发实际写库

### 修复

- stop 任务取消 pending 释放并发额度(已部署 v1.2.0,随本版本入档)

---
## v1.23.0 — 2026-08-10(格式校验失败带反馈重试 + stop/status 接口 + commit 状态守卫)

### 修复

- **skill 脚本格式校验失败纳入重试循环**:此前 LLM 输出合法 JSON 但缺字段
  (如 keywords[7].terms 缺失)直接 step_error 不重试;现在把校验错误反馈给
  LLM 重新生成,与 bad_json 共用重试预算(总上限 3 次,含首次),日志
  event=retry reason=format_error / format_error_final。

### 新增

- **POST /api/v1/groups/{id}/stop**:停止 generating 中的组。进程内
  asyncio.Task.cancel + 等待退出(防草稿覆盖竞态)+ 标 stopped 落草稿;
  已完成步骤产物保留;不计费;不重启容器。
- **GET /api/v1/groups/{id}/status**:轻量心跳,status/running/
  current_step/total_steps,判断"能否查方案组"(running=false 且
  status ∈ review/stopped/committed)。
- 状态枚举新增 `stopped`(graph/state.py)。

### 收紧

- **commit 仅允许 review 状态**:stopped/generating 组 commit 报 409,
  防止部分产物入库计费。

---
## v1.22.0 — 2026-08-10(修复 deepseek-v4-flash 工具调用循环 + 错误日志带原始返回 + 对接文档)

### 修复

- **step 3-6 全部 bad_json 失败**(生产 10.33.17.72 实测):deepseek-v4-flash
  绑定 load_skill 工具后,回合 1 发 tool_calls、回合 2 喂工具结果后**仍重复
  发 tool_calls 且 content 为空**,节点解析永远失败。四象限隔离实验确认罪魁
  是工具绑定(非 JSON Mode/prompt);修复 = 回合 2 换无工具绑定 LLM,方法论
  内容转普通消息喂入(graph/nodes.py),容器实测输出合法 JSON,生产全流程
  6 步验证通过(group 4c777cad,4 方案/10 轨/15 关键词)。

### 可观测性

- **bad_json 错误日志带原始返回**:重试记 tool_calls/content_len/content_head,
  最终失败记 content_head+content_tail,异常消息同样携带(progress 接口
  step_status[].error 可见)——此前只记"重试失败"结论,无法定位;
- **修日志文件重复行**:FileHandler 同时挂 uvicorn.error 与其父 uvicorn,
  启动/关闭类日志每行进文件两次;去掉 uvicorn.error 挂点(靠传播)。

### 文档

- `API.md`:完整接口文档,全部接口真实返回示例(生产实测数据);
- `INTEGRATION.md`:AI agent 可读对接规范(字段契约/状态机/错误处理/
  禁止事项/验收清单),开发人员可直接喂给 agent 实现对接。

---
## v1.21.0 — 2026-08-10(生产部署方案落地 —— Docker Compose + 并发加固)

### 部署

- **生产部署套件**(目标机 10.33.17.72,设计文档
  `docs/superpowers/specs/2026-08-10-sentiment-query-agent-prod-deploy-design.md`):
  - `deploy/` 自包含:Dockerfile(build context 仓库根)、docker-compose.yml
    (api:8000 + nginx:80)、nginx.conf(demo.html 静态托管)、deploy.sh
    (rsync 上机 → build → up → 健康检查)、README;不动根目录 compose;
  - 精简镜像依赖 `requirements-agent.txt`(版本锁自测试环境,不含
    torch/chromadb RAG 栈);
  - 日志落盘:`LOG_DIR` 环境变量开启 RotatingFileHandler(10MB×5),
    生产挂卷 `/home/logs/sentiment-query-agent/`;docker json-file 限 10MB×3;
  - CORS 生产收紧:`CORS_ORIGINS` 环境变量(逗号分隔源,缺省 `*` 兼容测试);
  - 数据持久卷 data/(checkpoint sqlite + 方案库 + 计费),回滚不丢数据。

### 并发加固(内部团队几十并发,单 worker + asyncio)

- **checkpoint sqlite 开 WAL**(`agent.py` / `api.py`):流水线写与进度轮询读
  走不同连接,防并发 `database is locked`;
- **scheme_store index.json 读-改-写加锁**:线程锁(进程内)+ fcntl 文件锁
  (跨进程预留)双保险,模式对齐 `billing.py`,防并发丢索引。

### 说明

- 单 worker 决策:多 worker 会破坏 index.json 与 sqlite 写锁;真实吞吐上限
  在 LLM rate limit 与计费防刷(每用户 5 pending),非 worker 数;
- 测试:`tests/test_sentiment_query_agent.py` 14 全过。

---
## v1.2.0 — 2026-08-07(轨 key 语义化 + 移除风险等级)

### 变更

- sentiment-query-agent:轨 key 语义化(a/b/c → 全量新闻/负面新闻/行业新闻),任务 ID 形如 Q0-全量新闻
- sentiment-query-agent:全链路移除风险等级(critical/high/medium/low):step6/state/nodes/converter/Excel/skill 文档/demo
- 保留:风险词(R 层词表、负面新闻轨 AND 条件)、频次定级、相关度 direct/indirect/context
- 兼容:LLM 多余 risk 输入被 step6 忽略;旧字母轨 key 校验失败记 GAP

---
## v1.2.0 — 2026-08-11(生产三错修复 + 推理模型调优)

### 修复

- **生产三错**:bad_json(增强 prompt 强制 JSON 输出)/ token 超限(step6 频次定级)/ terms 缺失(脚本默认值 + GAP 提醒)
- **max_tokens 调优**:16384 → 32768(4 倍余量,保证 48 条目完整输出)
- **thinking 参数踩坑**:加 `thinking: disabled` 反而触发服务端 8192 输出硬上限(实测 8191 截断);去掉后 max_tokens=32768 可正常输出 22704。**结论:传 max_tokens 不传 thinking**
- **step6 risk 字段丢失**:脚本缺 risk 输出(补 _common RISKS 枚举)+ nodes.py 合并逻辑漏 risk(补合并)

### 技术要点

- deepseek-v4-flash 推理模型:思考 token 计入 max_tokens 预算;思考模式默认开启,极端情况思考吃光预算(reasoning_tokens=65536 输出为 0)
- 生产部署 + 全流程测试通过(5 方案,risk/frequency 正常分级)

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
## v1.0.0 — 2026-08-07(正式交付)

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
