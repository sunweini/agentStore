# contract-review-agent 版本更新说明(CHANGELOG)

> 版本号独立管理(每 agent 独立序列)。
> 收尾规则:改动归本 agent → 更新本文件 + bump 版本号(当前最大号 +1)。

---

## v0.8.0 — 2026-08-14(计费切公共组件:agent_api_keys/agent_billing_records)

### 重构

- **计费/鉴权/apikey 管理上提公共组件**(`common.billing` / `common.auth` /
  `common.apikey_mgmt`,agent='contract'):删除 `billing.py` / `auth.py` /
  `apikey_mgmt.py` 三份重复实现(逐字重复),api.py 改 import 公共组件并补
  `"contract"` 参数。存储表从 `contract_api_keys` / `contract_billing_records`
  收敛到统一表 `agent_api_keys` / `agent_billing_records`((apikey, agent) 复合主键)。
  接口端点/请求参数/响应结构零变化(生产/测试已上线,对接方不受影响)。
- `deploy/init_tables.sql` 追加 `agent_api_keys` / `agent_billing_records` 两表
  (MySQL 方言,与 `common/db.py init_tables()` 一致);老表保留不删(回滚路径)。

---

## v0.7.0 — 2026-08-14(性能:必查法条相关性裁剪 + LLM 超时 + 进度回显)

### 新增

- **`store/law_store.py`**:`_priority_fragments(contract_type, query)` 按 2-gram
  相关性过滤必查法条(共享 ≥2 个 2-gram 才注入)。抬头/编号类章节 0 注入、
  社保章 1 条、试用/违约金章 4-6 条(原 8 条全量)→ 每章 prompt 减半,
  9 章合同审核从 5-13 分钟降到 ~3-6 分钟。
- **`common/llm.py`**(项目级):`get_chat_model(timeout=)` 支持请求超时,
  节点单章 LLM 120s 上限,防 DeepSeek 偶发挂起永久 running。
- **章节级进度回显**:`AgentState._progress_cb` 回调经 parse/review/verify/
  summarize;review 每章推进 current/total;API 后台线程更新 `_tasks`
  stage/current/total;SSE progress 事件带 JSON;`status` 接口返回 stage。
- **测试页健壮化**:review 响应头 `X-Task-Id` 立即取 task_id;status 轮询
  兜底(SSE 被代理缓冲也能回显);readSSE 容错;进度条 + "章节审核 第 N/M 章"。

### 修复

- 测试页 F2 审核对 FormData 漏发 apikey 头(422 根因)。

---

## v0.6.0 — 2026-08-14(检索调优:必查法条注入 + 引文修复)

### 新增

- **`store/law_store.py`**
  - `_PRIORITY` 必查法条清单(按领域):labor→劳动合同法 19/20/25/38/39/46/47/85 条;
    contract→民法典 496/497/563/577/584/585/586 条。`retrieve()` 确定性注入
    (从 `_exact` 取原文,标 `priority`),再补检索结果。修复"违约金/试用期/解除"
    等常见审核点检索带不出对应法条、statutory 结论被阻断的问题。
  - `_domain_of()`:合同类型子串匹配域(用户输入"买卖合同",alias 键是"买卖")。
  - `retrieve` 默认 k 5→8,bm25_weight 0.5→0.7(关键词精确优先)。
- **`graph/nodes.py`**
  - `_CHAPTER_SYSTEM` 强化:法律依据每项必须含 law_name/article_no/article_text 三字段。
  - `review_chapter` 解析兜底:LLM 漏填 law_name/article_no 时 `_repair_refs`
    按 article_text 标点归一化匹配片段补齐,statutory 不再因 schema 校验失败
    整章 findings 清空(静默漏审)。
- **测试**:必查法条注入 / 域子串匹配 / 引文缺字段修复 各 +1(46 passed)。

### 说明

- 检索向量区分度弱(违约金在劳动域 IDF 低)靠必查清单兜底,不做嵌入模型更换。

---

## v0.5.0 — 2026-08-13(Task 15:百度 OCR 接线)

### 新增

- **`utils/ocr_client.py`**
  - 修复 `ocr_image_bytes` base64 bytes→str(`base64.b64encode(img).decode("ascii")`;
    百度 API 需 base64 **字符串**,传 bytes 会 400)
  - 新增 `get_token()`:从 `common.config` 读 `BAIDU_OCR_API_KEY` / `BAIDU_OCR_SECRET_KEY`,
    缺任一返回空串(调用方据此报 `ocr_unconfigured`);凭据不进 git 不进日志
- **`graph/flows.py` `_parse_node`**:捕获 `NeedsOcrError` 后接线百度云端 OCR
  - 无 token(缺凭据)→ `ocr_unconfigured`(不调 OCR)
  - 有 token → `ocr_pdf_pages(file_path, token)` → 复用 `_looks_like_heading` 启发式
    分章(标题行 level=1 / 正文 level=0)→ `build_chapters` 产出 chapters
  - 取 token 失败 / OCR 识别异常 / 返回空文本 → `ocr_failed`(结构化日志只记
    error_type,不记 str(exc),防文件内容/接口返回敏感信息泄露)
- **测试**(tests/test_contract_review_agent.py 尾部):`get_token` 缺凭据返回空 /
  有凭据换 token / base64 str 修复回归 / parse 捕获 NeedsOcrError 后 OCR 产出章节 /
  无 token 不调 OCR / OCR 抛异常 / OCR 空文本,全 mock 不真调百度

### 说明

- `api.py` 无需改动:错误码路由已通用(error 字符串透传),`ocr_unconfigured` /
  `ocr_failed` 直接透出前端(result 端点 `{"error": ...}`)。
- OCR 文本质量调优(识别精度 / 分章)为后续版本,当前仅启发式分章即可。
- 文档同步:CLAUDE.md / API.md 移除"OCR 待接线"措辞,改为已接线。

### Follow-up(未实现,仅登记)

- **审核节点非 JSON 重试**:`graph/nodes.py` review 节点当前无畸形输出重试;
  spec §8 承诺"LLM 输出非 JSON → 重试(复用 sentiment 重试预算,上限 3 次)"待实现

### 测试

- `pytest tests/test_contract_review_agent.py -v` 全量绿

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

### 终审修正(合并前置,终审判定"可合入,带前置修正";v0.4.0 未发布,归入本段)

- **OCR 描述对齐真实实现**(API.md / CLAUDE.md):v1 扫描件(无文本层 pdf)返回
  `needs_ocr` 错误,`utils/ocr_client.py` 已封装**待接线**,OCR 接线为后续版本;
  删除"扫描件走百度云端 OCR"成功承诺;CLAUDE.md `POST /api/v1/laws/upload` 支持
  格式 docx/pdf/txt → md/txt(实现仅 decode utf-8 文本);`agent.py` 架构行去掉
  "占位"字样
- **反幻觉 footgun 修复**(终审 finding #6,`graph/flows.py`):`build_graph(law_store=None)`
  原为 None 时 `_verify` 透传不核验;langgraph.json 注册的 `agent.py:build_graph`
  恰为无参调用 → 经 langgraph server 跑图会静默关闭校验层。改为 None 时缺省用
  `_default_law_store()`(函数体内 import,避免模块级循环 import)并移除 `_verify`
  的 None 透传分支 —— 任何路径构造的图校验层恒开启
- **部署 seed 步骤**(deploy/):`deploy.sh` 新增第 6 步 —— 健康检查后若生产法条
  向量库空则 `seed_laws --if-empty` 灌一次(幂等,非空跳过;失败不中断部署,仅警告);
  `scripts/seed_laws.py` 新增 `--if-empty` 标志 + `LawStore.vector_count()`
  (chromadb 公开 `get()` 计数,不触发嵌入);`deploy/README.md` 补充 seed/拷贝 dev
  数据两种初始化方案与 `EMBEDDING_*` 一致前提

### Follow-up(未实现,仅登记)

- **OCR 接线**:`utils/ocr_client.py` 已封装待接线,当前无文本层 pdf 返回
  `needs_ocr`。接线需:①依赖百度真实凭据(`BAIDU_OCR_*`)联调 ②修复
  `ocr_image_bytes` 的 base64 bytes→str(`base64.b64encode` 返回 bytes,需
  `.decode("ascii")`)后,接入流水线 `_parse_node` 的 `NeedsOcrError` 分支
- **审核节点非 JSON 重试**:`graph/nodes.py` review 节点当前无畸形输出重试;
  spec §8 承诺"LLM 输出非 JSON → 重试(复用 sentiment 重试预算,上限 3 次)"待实现

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
