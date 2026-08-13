# 合同审核 Agent 设计

- 日期:2026-08-13
- 状态:设计定稿,待用户 review
- agent:`agents/contract_review_agent`

## 1. 背景与目标

构建合同审核 Agent,两个功能:

1. **F1 审核 prompt 优化**:用户输入合同类型 + 原始审核 prompt,Agent 优化为结构化审核 prompt 返回(含类型常见风险点 + 法规引用指引)。
2. **F2 合同审核**:用户上传 word / pdf 合同,Agent 按章节审核,输出每处问题的原文位置、问题描述、改进建议、法律依据,按固定格式返回。

核心约束:**严格遵守法规条款,不允许编造,不允许幻觉**。所有法律依据必须可回溯到法条库原文。

## 2. 需求与约束

### 2.1 功能需求

| 编号 | 需求 |
|---|---|
| F1 | 输入合同类型 + 用户原始 prompt → 输出结构化审核 prompt。temperature ≤ 0.2。 |
| F2 | 输入 word/pdf 合同 + 审核要求(可用 F1 产物)→ 章节级审核报告。temperature 固定 0.1。 |
| R1 | 反幻觉:每条法律依据必须可核验,引用必须来自法条库原文。 |
| R2 | 文件大小限制:文件 ≤ 2MB 且正文 ≤ 5 万字,超限明确报错。暂不支持超长文分段审核。 |
| R3 | 交付:FastAPI + apikey 配额计费 + 生产部署(复刻 sentiment 模式)。 |
| R4 | 文件类型:docx / pdf(文本层)+ 扫描件 OCR(无文本层时)。 |

### 2.2 明确不做(第一版)

- 超长文(>5 万字)分段跨块审核
- OCR 之外的复杂版式还原(表格、页眉页脚精排)
- 法规条款的自动更新/增量同步
- 多合同批量审核

## 3. 架构总览

```
POST /contract/review(文件 + 合同类型 + 审核要求)
        │
        ▼
┌─────────────────────────────┐
│ 文件解析层                   │
│ docx → python-docx          │
│ pdf  → pypdf(文本层)         │
│        无文本层 → PaddleOCR   │
│ 输出 Document{chapters[]}    │
└────────────┬────────────────┘
             ▼
┌─────────────────────────────┐
│ 章节审核(并行/顺序)           │
│ 每章:检索法条 → LLM(temp=0.1) │
│ 输出 chapter_findings        │
└────────────┬────────────────┘
             ▼
┌─────────────────────────────┐
│ 引用校验层(核心反幻觉)        │
│ 每条法律依据:条号存在 +       │
│ 引文 fuzzy match 库内原文     │
│ 通过→保留 / 失败→降级建议     │
└────────────┬────────────────┘
             ▼
┌─────────────────────────────┐
│ 汇总节点                     │
│ 合并 findings → 风险排序      │
│ 输出 JSON + markdown 报告    │
└────────────┬────────────────┘
             ▼
    报告 + 法条库版本声明
```

LangGraph 图(与 sentiment 同风格):

```
START → parse → review_chapters → verify_refs → summarize → END
```

## 4. 组件设计

### 4.1 文件解析层(`utils/document_parser.py`)

统一输出:

```python
class Chapter(BaseModel):
    title: str          # 章节标题
    level: int          # 标题层级(1/2/3)
    order: int          # 章节序号
    text: str           # 该章节正文

class Document(BaseModel):
    chapters: list[Chapter]
    total_chars: int
    source_type: str    # docx / pdf
```

- **docx**:`python-docx` 遍历段落,识别标题样式(`Heading 1/2/3` 或 `w:outlineLvl`),正文段落挂到最近章节。
- **pdf**:`pypdf` 提取文本层。按字号/行文启发式分章(第一版:无目录结构的 PDF 若无法可靠分章,降级为单章全文,审核粒度仍可用)。
- **无文本层 pdf**:检测提取文本为空/过短 → 标记需 OCR,走 PaddleOCR。
- **大小校验**:解析前校验文件 ≤2MB;解析后校验总字数 ≤5 万字,超限报错 `CONTRACT_TOO_LONG`。

OCR 策略:

- OCR 独立依赖(`requirements-ocr.txt`),独立容器或可选镜像层,不进主镜像。
- 第一版 OCR 用 PaddleOCR(CPU 可跑)。检测到扫描件时,`/contract/review` 走 OCR 增强路径。
- OCR 结果同样走章节化(按空白行/标题启发式)。

### 4.2 法条库(`store/law_store.py` + `scripts/seed_laws.py`)

- **存储**:复用 `common/rag.py` 模式,Chroma collection `contract_law`。
- **元数据**:`{law_name, article_no}`。
- **内置种子(第一版)**:
  - 《中华人民共和国劳动法》全文(107 条)
  - 《中华人民共和国劳动合同法》全文(98 条)
  - 《中华人民共和国民法典》合同编高频条款(通则、买卖、租赁、承揽、借款核心,约 100 条)
  - 法条文本**人工从权威来源采集**(国家法律法规数据库 flk.npc.gov.cn、全国人大官网),严禁 LLM 生成/记忆填充。
  - seed 脚本逐条记录 `来源 URL + 采集日期`,作为元数据入库。
- **用户补充**:`POST /laws/upload` 上传法规文档(docx/pdf/txt)→ 解析为条目 → 灌库。条号重复则覆盖(同 law_name + article_no 唯一)。
- **检索**:章节文本 → 向量 + 关键词混合检索(复用 rag.py 的 BM25+RRF 实现)top-K 法条片段。

### 4.3 章节审核节点(`graph/nodes.py`)

- system prompt = F1 产出的结构化审核 prompt(或用户直接给的审核要求)+ 检索法条片段。
- 每章独立调用 LLM,temperature **固定 0.1**,强制 JSON mode。
- 每章输出:

```json
{
  "chapter": "第五章 违约责任",
  "findings": [
    {
      "原文引用": "5.2 条:一方违约,应按合同总额的 5% 支付违约金。",
      "风险类型": "合规|权益|漏洞|歧义",
      "问题描述": "违约金比例可能超出法定上限。",
      "改进建议": "调整为不超过损失的 30%……",
      "法律依据": [
        {"law_name": "民法典", "article_no": "第五百八十五条", "article_text": "约定的违约金过分高于造成的损失的,人民法院……"}
      ],
      "confidence": "statutory"
    }
  ]
}
```

- **无法律依据的发现**:`法律依据=[]`、`confidence="suggestion"`,汇总时明确标注"仅提示,非强制"。

### 4.4 引用校验层(`graph/verify.py`)(核心)

对每条 `法律依据`:

1. **条号存在性**:`law_name + article_no` 在库中查询,不存在 → 剔除/降级。
2. **引文一致性**:LLM 输出的 `article_text` 与库内原文做 fuzzy match(归一化后相似度阈值,如 difflib ratio ≥ 0.8),不一致 → 用库内原文替换。
3. 校验失败策略:
   - 条号不存在 → 该条 `法律依据` 移除,对应 finding 降级为 `suggestion`,标注"引用未能核验"。
   - 引文不一致 → 替换为库内原文(LLM 只做定位,不做改写)。
4. 校验层是纯代码,无 LLM 参与,保证确定性。

**反幻觉闭环**:审核节点只允许引用检索返回的法条片段;校验层逐条核验;输出报告声明法条库版本。任何无法核验的内容不进入 `statutory` 结论。

### 4.5 汇总节点(`graph/nodes.py`)

- 合并各章 findings,按 `风险类型` 与严重度排序。
- 输出:
  1. 结构化 JSON(全量 findings)
  2. markdown 报告(指定格式模板,见 §6)
- 报告头:法条库版本 + 文件信息 + 审核时间。

### 4.6 F1 prompt 优化(`graph/prompt_node.py`)

- 输入:合同类型 + 用户原始 prompt。
- LLM(temperature ≤ 0.2,建议 0.1)产出:
  - 角色定义
  - 审核范围(逐条/逐章)
  - 类型常见风险清单(内置知识 + 法条库对应检索)
  - 输出格式要求(对齐 §4.3 Schema)
  - 引用法规指引(只允许引用库内法条)
- 产物可直接作为 F2 的审核要求复用。

## 5. 配额与计费

- 复用 sentiment 的 `billing.py` / `auth.py` / `common/db.py`。
- **计费单位:按次**。一个合同文件审核完成 = 1 次扣费(commit 时事务原子扣减)。
- F1 prompt 优化:计费待定,默认不计费或 0.5 次(写 spec 时定,倾向不计费)。
- 配额:pending 上限复用现有模型(每 apikey 5)。

## 6. 输出报告格式(markdown 模板)

```markdown
# 合同审核报告

- 合同名称:xxx.docx
- 审核依据:内置法条库 v1(劳动法 / 劳动合同法 / 民法典合同编)
- 审核时间:2026-08-13 14:00
- 风险结论:高风险 2 处 / 中风险 3 处 / 提示 4 处

## 一、高风险

### 1.1 [章节] 第五章 违约责任
**原文引用**:5.2 条:一方违约,应按合同总额的 5% 支付违约金。
**问题**:违约金比例可能超出法定上限。
**建议**:……
**依据**:《民法典》第五百八十五条——"约定的违约金过分高于造成的损失的,人民法院可以根据……"
（法律依据已核验）

## 二、中风险
……（同上结构）

## 三、提示（仅提示,非强制）
### 3.1 [章节] 第一章 定义
**原文引用**:……
**问题**:……
**建议**:……
（无法律依据,标注 suggestion）
```

## 7. API 设计

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/v1/contract/review` | POST | 上传文件 + contract_type + prompt → 提交审核任务,返回 task_id(SSE 流式回显章节进度) |
| `/api/v1/contract/status` | GET | 任务状态(解析中/审核中/完成/失败 + 进度) |
| `/api/v1/contract/result` | GET | 最终报告(JSON + markdown) |
| `/api/v1/contract/stop` | POST | 停止任务(复用 sentiment stop 模式) |
| `/api/v1/contract/prompt` | POST | F1:合同类型 + 原始 prompt → 优化后 prompt |
| `/api/v1/laws/upload` | POST | 用户补充法条库 |
| `/api/v1/laws` | GET | 法条库列表(law_name/条数/版本) |

鉴权:apikey(复用 `auth.py`)。文件上传:multipart,限制 ≤2MB。

## 8. 错误处理

| 错误 | 处理 |
|---|---|
| 文件 >2MB / 正文 >5 万字 | 报错 `CONTRACT_TOO_LONG`,提示分段 |
| 非 docx/pdf | 报错 `UNSUPPORTED_TYPE` |
| 扫描件 OCR 失败 | 报错 `OCR_FAILED`,提示重传清晰扫描件 |
| 法条库为空/未命中 | 审核可继续,标注"无相关法条",不编造 |
| LLM 输出非 JSON | 重试(复用 sentiment 重试预算,上限 3 次) |
| 引用无法核验 | 降级 suggestion,报告标注 |

## 9. 测试策略

| 测试 | 覆盖 |
|---|---|
| 文件解析 | docx 章节树 / pdf 文本层 / 无文本层→OCR 标记 / 大小超限报错 |
| 章节构建 | 标题层级、正文归属、无标题降级单章 |
| **引用校验层**(核心) | 伪造条号→剔除;引文不一致→替换库内原文;相似度阈值边界 |
| 法条 seed | 条号唯一、law_name+article_no 主键、来源版本记录 |
| F1 prompt 优化 | 输入类型+prompt→结构化输出,JSON 合法 |
| 端到端 | 样例劳动合同 docx → 完整报告,statutory 结论全部可核验 |
| 计费 | 审核完成扣 1 次,失败不扣,配额耗尽拒绝 |

## 10. 技术栈与依赖

- 复用:Python + LangChain/LangGraph + DeepSeek(`common/llm.py`)+ `common/rag.py`(BM25+RRF)+ billing/auth/db。
- 新增依赖:
  - `python-docx`(docx 解析)
  - `pypdf`(pdf 文本层)
  - `paddleocr` + `paddlepaddle`(独立 `requirements-ocr.txt`,不进主镜像)

## 11. 部署

- 复刻 sentiment 部署套件:`agents/contract_review_agent/deploy/`(Dockerfile/compose/deploy.sh/init_tables.sql)。
- OCR 容器独立服务或镜像层,主服务启动时探测 OCR 服务可用性。
- 端口/日志/回滚按 sentiment 惯例。

## 12. 目录结构

```
agents/contract_review_agent/
├── agent.py              # build_graph 入口
├── api.py                # FastAPI 接口
├── graph/
│   ├── state.py          # AgentState + finding 模型
│   ├── nodes.py          # 解析/章节审核/汇总节点
│   ├── verify.py         # 引用校验层(核心)
│   └── flows.py          # 图构建
├── utils/
│   ├── document_parser.py# docx/pdf/OCR 解析
│   └── chapterizer.py    # 章节树构建
├── store/
│   ├── law_store.py      # 法条库(Chroma)
│   └── task_store.py     # 任务/报告存储(JSON 文件库,复用 sentiment 模式)
├── skills/               # 审核方法论 skill(可选)
├── data/laws/            # 内置法条 seed 文本
├── scripts/
│   └── seed_laws.py      # 法条灌库脚本
├── deploy/               # 生产部署套件
├── prompts/              # F1 prompt 模板
├── CLAUDE.md
├── CHANGELOG.md
└── API.md
```

## 13. 开放问题(实现前确认)

1. OCR 引擎最终选型:PaddleOCR CPU 版(慢,~每页数秒)vs 对接已有 OCR 服务。默认 PaddleOCR。
2. F1 prompt 优化是否计费。默认不计费。
3. ~~法条 seed 文本采集~~ 已定:劳动两法全文 + 合同编高频条款,人工从权威来源采集,来源 URL 记入元数据。
