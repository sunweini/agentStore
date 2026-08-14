# contract-review-agent 开发指南

合同审核 Agent:双功能 —— **F1 审核 prompt 优化**(合同类型 + 原始审核 prompt → 结构化审核 prompt)与 **F2 合同章节审核**(上传 word/pdf 合同 → 章节级审核报告,每处问题含原文位置/问题描述/改进建议/法律依据)。核心铁律:**反幻觉** —— 所有法律依据必须可回溯到法条库原文,不允许编造。

## 本 agent 是什么

- 职责:把"审一份合同"的模糊需求,变成可核验的章节级审核报告;F1 产物可直接作 F2 审核要求复用。
- 开发前必读:根目录 [CLAUDE.md](../../CLAUDE.md) 和 [docs/dev-standards.md](../../docs/dev-standards.md)(必须依据 langchain MCP 文档/API 开发)。

## 架构

章节审核流水线 + 引用校验层(核心反幻觉),LangGraph 与 sentiment 同风格:

```
START → parse → review_chapters → verify_refs → summarize → END
 解析    逐章审核(temp=0.1)      引用校验(纯代码)     汇总报告
```

| 文件 | 职责 |
|---|---|
| [agent.py](agent.py) | 图构建入口:`build_graph()`(已实现,langgraph.json 注册入口;含 `run_review` / `_default_law_store`) |
| [api.py](api.py) | FastAPI:review/status/result/stop/prompt/laws/apikeys 8 接口 + 公共计费组件(agent='contract') |
| 计费/鉴权/apikey 管理 | 公共组件 [`common/billing.py`](../../common/billing.py) / [`common/auth.py`](../../common/auth.py) / [`common/apikey_mgmt.py`](../../common/apikey_mgmt.py)(agent='contract',统一表 agent_api_keys/agent_billing_records) |
| [graph/state.py](graph/state.py) | AgentState + finding 模型(原文引用/风险类型/建议/法律依据/confidence) |
| [graph/nodes.py](graph/nodes.py) | parse / review_chapters / summarize 节点(LLM 强制 JSON,temp 固定 0.1) |
| [graph/verify.py](graph/verify.py) | **引用校验层(核心)**:条号存在 + 引文 fuzzy match(≥0.8),失败降级 suggestion;纯代码无 LLM |
| [graph/flows.py](graph/flows.py) | 图构建:parse → review_chapters → verify_refs → summarize 顺序边 |
| [utils/document_parser.py](utils/document_parser.py) | docx(python-docx)/ pdf(pypdf 文本层,无文本层抛 NeedsOcrError→流水线转百度 OCR)→ Document{chapters[]},≤2MB/≤5 万字校验 |
| [utils/chapterizer.py](utils/chapterizer.py) | 章节树构建:标题层级识别,无标题降级单章全文 |
| [utils/ocr_client.py](utils/ocr_client.py) | 百度智能云通用文字识别封装(已接线:`get_token` 换 token + `ocr_pdf_pages` 逐页 OCR;缺凭据返回 `ocr_unconfigured`,失败 `ocr_failed`) |
| [store/law_store.py](store/law_store.py) | 法条库:data/laws/*.md 权威真源 + Chroma 向量检索;语义检索(审核)+ 精确核验(校验层)两路径 |
| [store/task_store.py](store/task_store.py) | 任务/报告存储(JSON 文件库,复用 sentiment scheme_store 模式) |
| [scripts/seed_laws.py](scripts/seed_laws.py) | 法条灌库:md → 条目(条号+原文)→ Chroma,记录来源 URL + 采集日期 |
| [data/laws/](data/laws/) | 内置法条源文本(人工采集,权威真源,严禁 LLM 生成) |

## 常用操作

- **加法条(内置 seed)**:`scripts/seed_laws.py` 灌 `data/laws/*.md` 入 Chroma;seed 文本人工从权威来源采集(flk.npc.gov.cn / 全国人大官网),逐条记来源 URL + 采集日期,严禁 LLM 生成/记忆填充。
- **用户补充法条**:`POST /api/v1/laws/upload`(md/txt 纯文本)→ 解析条目灌库;条号重复覆盖(同 law_name + article_no 唯一)。
- **改审核 prompt**:`graph/nodes.py` 的 review_chapters 节点(F1 产出的结构化 prompt 或用户审核要求 + 检索法条片段注入;ChatPromptTemplate 是 f-string 语法,JSON 样例 `{}` 转义 `{{}}`,见 dev-standards §7.2)。
- **改引用校验阈值**:`graph/verify.py`(条号存在性 + 引文 fuzzy match 相似度阈值,difflib ratio ≥ 0.8)。
- **配百度 OCR**:`.env` 配 `BAIDU_OCR_API_KEY` / `BAIDU_OCR_SECRET_KEY`(凭据不进 git);扫描件(无文本层 pdf)自动走百度云端 OCR 分章后照常审核;缺凭据返回 `ocr_unconfigured`,OCR 失败返回 `ocr_failed`。
- **配配额/鉴权(公共组件)**:`.env` 的 `MYSQL_URL`(agentstore 库,统一表
  agent_api_keys/agent_billing_records,agent='contract')+ `ADMIN_APIKEY`;api.py
  调 `common.billing` / `common.auth` / `common.apikey_mgmt`(agent='contract'),生产
  MySQL 建表走 `deploy/init_tables.sql`(含 agent_* 两表);存储访问统一走
  `common/db.py`(MySQL 生产 / SQLite 测试双后端),业务代码不直接连库。
- **配 embedding 模型**:`.env` 的 `EMBEDDING_*` 组(默认 huggingface 本地 bge-small-zh-v1.5;换模型后必须 drop `data/contract-rag` 重灌)。
- **跑测试**:`pytest tests/test_contract_review_agent.py -v`(解析/校验层/seed/计费/接口单测,SQLite 后端;图/端到端需外部服务)。
- **收尾更新 CHANGELOG**:改动归本 agent → 写本 agent 的
  `agents/contract_review_agent/CHANGELOG.md`,bump 版本号(当前最大号 +1,现 v0.8.0 → 下版 v0.9);
  纯项目级(common/依赖)→ 根 `CHANGELOG.md` 项目级区。
- **启动 API**:`uvicorn agents.contract_review_agent.api:app --reload`(配额/计费功能需配 MYSQL_URL)。
- **部署命名规范**:compose 项目名 `deploy-contract-review-agent`(规范 `deploy-<agent>`,见根 CLAUDE.md 架构约定),端口 `CONTRACT_PORT` 环境变量驱动(生产 8000 / 测试 8001 避让 sentiment);deploy 用 `bash agents/contract_review_agent/deploy/deploy.sh`,测试环境 `PORT=8001`。详见 deploy/README.md。

## 约束

- **反幻觉铁律**:审核节点只允许引用检索返回的法条片段;校验层逐条核验;任何无法核验的内容不进入 statutory 结论,降级 suggestion 并在报告标注;输出报告声明法条库版本。法条文本只来自 `data/laws/` 权威真源,严禁编造。
- **temperature**:F1 ≤ 0.2,F2 固定 0.1(经 `common/llm.py` 工厂,不直接 new)。
- **大小限制**:文件 ≤2MB 且正文 ≤5 万字,超限明确报错 `CONTRACT_TOO_LONG`,提示分段;暂不支持超长文分段审核;非 docx/pdf 报 `UNSUPPORTED_TYPE`;扫描件(无文本层 pdf)自动走百度云端 OCR(需 `.env` 配 `BAIDU_OCR_*`;缺凭据 `ocr_unconfigured`,失败 `ocr_failed`)。
- **计费/鉴权走公共组件**:api.py 调 `common.billing` / `common.auth` /
  `common.apikey_mgmt`,agent 固定 'contract',统一表 agent_api_keys /
  agent_billing_records((apikey, agent) 复合主键,与 sentiment 同表同 schema,
  额度按 agent 维度隔离);接口端点/参数/返回不变。本 agent 不再有独立
  billing.py / auth.py / apikey_mgmt.py(v0.8.0 已删)。
- **langchain MCP 铁律**:开发前必须查 docs-langchain / reference-langchain MCP 确认 API 用法,禁止凭记忆写 API(见根 CLAUDE.md)。
- **LLM 畸形输出重试(spec §8 承诺,待实现)**:非 JSON 输出重试(复用 sentiment 重试预算,上限 3 次);当前 review 节点无重试,见 CHANGELOG follow-up。
- 用户标识(apikey)只进日志/计费,不进 span label(OTel 高基数约束,OBS-CORE-003)。
