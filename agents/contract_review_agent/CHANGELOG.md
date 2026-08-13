# contract-review-agent 版本更新说明(CHANGELOG)

> 版本号独立管理(每 agent 独立序列)。
> 收尾规则:改动归本 agent → 更新本文件 + bump 版本号(当前最大号 +1)。

---

## v0.4.0 — 2026-08-13(Task 14:文档收尾 + 部署套件 + minor 清理)

### 新增

- **API.md**:全接口文档(仿 sentiment API.md),11 接口逐个请求/鉴权/响应真实示例
  - F2 章节审核链:POST /api/v1/contract/review(multipart + SSE 进度)/ status / result / stop
  - F1:POST /api/v1/contract/prompt(不计费)
  - 法条:GET /api/v1/laws、POST /api/v1/laws/upload(管理员)
  - apikey 管理 3 接口:POST/GET /api/v1/apikeys、DELETE /api/v1/apikeys/{apikey}(admin 头)
  - GET /health + 错误码汇总 + 计费规则
- **deploy/**:部署套件(尚未上机,README 标注)
  - `Dockerfile`:精简依赖(无 torch/无本地 OCR,嵌入走远程 openai-compatible);含 python-docx/pypdf + langchain-chroma(法条向量库必需)
  - `requirements-agent.txt`:版本锁自测试环境;langchain-huggingface 仅 base 依赖(懒加载 sentence-transformers,不拉 torch)
  - `docker-compose.yml`:API 8000,env_file 仓库根 .env(占位:MYSQL_URL/ADMIN_APIKEY/BAIDU_OCR_API_KEY/BAIDU_OCR_SECRET_KEY/EMBEDDING_*)
  - `deploy.sh`:rsync 上机 → build → compose up → 健康检查(路径 /opt/contract-review-agent,host 参数环境变量可覆盖)
  - `init_tables.sql`:MySQL 建 contract_api_keys / contract_billing_records 两表(与 common/db.py SQLite 结构对齐)
  - `README.md`:用法/首次部署前置/端口/回滚/注意

### 修复(minor 清理,controller 定案)

- `graph/flows.py` `_summarize`:审核时间硬编码 `"2026-08-13"` → `datetime.now().strftime("%Y-%m-%d %H:%M")`
- `graph/flows.py` `_parse_node` 的 `except Exception` 兜底:加结构化日志
  `event=parse_unexpected_error`(key=value,OBS-CORE-001;只记 error_type 不记 str(exc),防敏感信息)
- `store/law_store.py` `list_laws()` **source_url 返回首条正文的 bug**:新增 `_source_urls`
  dict(seed/load_bundled 时按 law_name 填权威源 URL),list_laws 返回真 URL 而非正文首段
- `CLAUDE.md`:启动命令 `api:create_app --factory` → `uvicorn agents.contract_review_agent.api:app --reload`

### 测试

- `pytest tests/test_contract_review_agent.py -v` 全量绿

---

## v0.3.0 — 2026-08-13(Task 13:内置法条种子 + 校验层运行时可用性加固)

### 新增

- **data/laws/**:内置权威法条源(人工采集,逐字拷贝,严禁 LLM 生成)
  - `labor_law.md`(劳动法 107 条,2018-12-29 修正)、`labor_contract_law.md`
    (劳动合同法 98 条,2012 修正)、`civil_code_contract.md`(民法典合同编高频
    89 条,2021-01-01 施行);每部含 `来源:` URL + `采集日期:` 可回查
  - `.gitignore` 放行 `agents/contract_review_agent/data/` 入库(根 `data/` 仍忽略)
- **`store/law_store.py` `load_bundled(laws_dir)`**:构造即从 `data/laws/*.md`
  加载 `_exact` 精确索引(不灌向量);`LawStore.__init__(data_dir, laws_dir=None)`
  新增 `laws_dir` 参数
- **`seed()` 分批灌库**:> `_BATCH`(16)条自动分批,413/424/5xx/连接失败指数
  退避重试(`_retryable` 判定,最多 6 次)——修复嵌入服务单批上限 32 撞 413
- **api.py / agent.py**:`_law_store` / `_default_law_store()` 均传
  `laws_dir` → 生产运行时校验层 `verify_ref` 不再因未 seed 全降级「引用未能核验」,
  `GET /api/v1/laws` 返回内置三法
- **scripts/seed_laws.py**:灌库后打印 `list_laws()` 摘要(核验 `_exact` 填充)
- **测试**:`test_load_bundled_from_md_dir` / `test_load_bundled_builtin_laws`
  (不 seed 也核验通过)/ `test_seed_batching_retry`(分批 + 413 重试)

### 说明

- 法条来源 mofcom.gov.cn(劳动/劳动合同法)、bjrd.gov.cn(民法典);
  flk.npc.gov.cn 对直接 curl 不可达改用上述官方政务源(现行有效文本)。
- 已知基础设施风险(超出本任务,报告另述):远端嵌入服务向量区分度不足 →
  违约金等章节检索难以命中对应法条,statutory 结论被检索层阻断。

---

## v0.2.1 — 2026-08-13(Task 12:FastAPI 接口)

### 新增

- **api.py**:11 接口 + 独立 apikey 配额计费接线
  - `POST /api/v1/contract/review`:multipart 上传 docx/pdf(≤2MB 写盘前校验,防公网 DoS)+ SSE 流实时章节进度;后台线程跑 run_review → commit 扣 1 单位
  - `GET /api/v1/contract/status` / `GET /api/v1/contract/result`(JSON + markdown 报告)/
    `POST /api/v1/contract/stop`(cancel_pending 释放并发额度,不扣费)
  - `POST /api/v1/contract/prompt`(F1,不计费)/ `GET /api/v1/laws` / `POST /api/v1/laws/upload`(管理员)
  - apikey 管理:POST/GET /api/v1/apikeys、DELETE /api/v1/apikeys/{apikey}(仅管理员,admin 头)
  - `GET /health`
- **任务生命周期**:running → done(commit 成功)/ failed(异常/错误,兜底 failed + cancel_pending + unlink)/ cancelled(stop,线程完成前标记,跳过 commit 不覆写状态)
- **安全加固(审查 Critical/Important)**:后台线程异常绝不静默退出(否则 pending 槽位泄漏/临时文件残留);任务归属校验(非本人 apikey → 404,不泄露存在性);commit 失败/解析失败日志只记 error_type 不记 str(exc)(防 apikey/文件内容凭据泄露);apikey 脱敏进日志
- **测试**:health / 未鉴权 401 / 后台异常兜底 / 任务归属校验

---

## v0.2.0 — 2026-08-13(Task 11:独立计费/鉴权/配额)

### 新增

- **auth.py**:独立 apikey 鉴权(contract 独立体系,不复用 sentiment)
  - `check_apikey(apikey)`:contract_api_keys 存在 + active 校验,无效/删除 → 401
  - `require_admin(apikey)`:role != 'admin' → 403
- **billing.py**:独立配额与计费(contract_api_keys / contract_billing_records)
  - `init_db()`(建全表)/ `check_quota`(免费+付费剩余 ≤0 → 403)/
    `create_pending`(并发 pending 上限 5 → 429)/ `commit`(扣 1 单位,先免费后付费,事务原子)/
    `cancel_pending` / `usage`
- **apikey_mgmt.py**:独立 apikey 管理
  - `create_apikey(name, role)`(生成随机 sk- apikey,默认免费 10 / 付费 0)/
    `admin_list(apikey)`(管理员)/ `deactivate_apikey(apikey, admin)`(软删)
- **测试**:`test_billing_flow` / `test_commit_then_cancel_frees_pending`(SQLite 临时库,不碰生产 MySQL)
- common/db.py 新增 contract_ 两表(项目级改动,记根 CHANGELOG)

---

## v0.1.4 — 2026-08-13(Task 9-10:F1 prompt 优化 + LangGraph 图构建)

### 新增

- **graph/prompt_node.py** F1:合同类型 + 原始审核 prompt → 结构化审核 prompt
  (temperature 0.2;默认五段式模板,含引用指引"禁止编造/无依据标注仅提示非强制")
- **graph/flows.py** `build_graph`:parse → review → verify → summarize 顺序图;
  parse 失败条件路由(needs_ocr / too_long / unsupported → END,不中断整图)
- **agent.py** `run_review`:同步跑完整图,返回 {report, report_json, error};
  法条库缺省用 data/contract-rag
- **测试**:build_graph 冒烟 / run_review 超长文件报错

---

## v0.1.3 — 2026-08-13(Task 6-8:章节审核节点 + 引用校验层 + 汇总报告)

### 新增

- **graph/nodes.py** `review_chapter`:每章检索法条片段(领域过滤,k=5)+ LLM(temperature
  0.1)强制 JSON,反幻觉系统提示(只能引用法条片段、无依据 confidence=suggestion)
- **graph/verify.py** 引用校验层(纯代码,无 LLM):条号存在 + 引文 difflib ratio ≥ 0.8;
  条号不存在 → 移除依据降级 suggestion 追加"(引用未能核验)";引文不符 → 用库内原文替换
- **graph/report.py** 汇总:风险分级(合规→高风险/权益·漏洞·歧义→中风险/无依据→提示)
  + markdown 报告(声明法条库版本/审核时间/风险结论)+ 结构化 JSON(stats)
- **测试**:mock LLM 节点装配 / 校验层精确核验、降级、原文替换 / 报告分级与统计

---

## v0.1.2 — 2026-08-13(Task 4-5:文件解析层 + 百度 OCR 云端)

### 新增

- **utils/chapterizer.py** 章节树:标题层级识别构建章节,无标题降级单章全文
- **utils/document_parser.py**:docx(python-docx)/ pdf(pypdf 文本层)→ Document{chapters[]};
  大小限制 ≤2MB / 正文 ≤5 万字(ContractTooLongError);非 docx/pdf → UnsupportedTypeError
- **utils/ocr_client.py** 百度智能云 OCR(云端接口,零本地模型):get_baidu_token /
  ocr_image_bytes / ocr_pdf_pages(PyMuPDF 可选,未装返回 None);凭据 BAIDU_OCR_*
- **测试**:章节构建 / 无标题降级 / 类型拒绝 / 超限 / OCR token 与行拼接(mock HTTP)

---

## v0.1.1 — 2026-08-13(Task 2-3:法条库双存储 + 领域过滤 + md 解析)

### 新增

- **store/law_store.py** LawStore 双存储(设计 §4.2):
  1. 向量库(collection `contract_law`,Chroma):法条按"条"粒度 embedding 入库,语义检索用
     (审核节点按领域过滤 + BM25/向量混合);内建 _LawRagClient 子类放开集合名校验
  2. 源文件精确索引 `_exact`:按 law_name 建 {article_no: 原文} 内存索引,校验层取逐字原文
- **领域硬过滤**:DOMAIN_ALIASES 合同类型 → 领域(labor/contract),retrieve 按领域筛法条
- **utils/law_parser.py** `parse_law_md`:md → LawArticle 列表(条号+原文+来源 URL+采集日期+
  领域);非法条目标记跳过,缺 law_name 记 errors
- **测试**:seed / list_laws / 领域过滤 / 未知类型不过滤 / verify_ref 精确命中与缺失

---

## v0.1.0 — 2026-08-13(项目初始化:目录结构 + 占位文档 + langgraph 注册)

### 新增

- **骨架**:`agents/contract_review_agent/` 目录结构,全部 .py 写占位 docstring
  (职责 / 待实现 / 设计文档引用),不含实现代码
- **双功能定义**:F1 审核 prompt 优化 + F2 合同章节审核(详见设计文档
  `docs/superpowers/specs/2026-08-13-contract-review-agent-design.md`)
- **langgraph.json 注册**:`contract_review_agent` → `agent.py:build_graph`
  (保留现有 sentiment-query-agent / kingdee_plugin_agent 注册不动)
- **CLAUDE.md**:本 agent 职责 / 架构 / 常用操作 / 约束(反幻觉铁律 / temperature /
  大小限制 / 独立计费)
- **测试占位**:`tests/test_contract_review_agent.py`(仅包导入冒烟,行为测试待实现阶段)
