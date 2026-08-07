# sentiment-query-agent 优化设计:轨 key 语义化 + 移除风险等级

日期:2026-08-07
状态:已确认(用户逐节批准)
前置:设计见 `2026-08-06-sentiment-query-agent-sentiment-query-agent-design.md`(本设计为增量修改)

## 1. 背景与目标

用户对 sentiment-query-agent 的 skill 与输出提出两点优化:

1. **整个 skill 过程中不需要风险等级**——不判断风险等级,输出结果和 Excel 文件也不含风险等级。
2. **轨 key 语义化**——现输出 `Q0-a`/`Q0-b`/`Q0-c` 的字母分级用户无法理解,需要改成语义化轨名。用户确认 `Q0-快讯`/`Q0-司法`/`Q0-招标` 这类语义名是想要的样式。

明确保留项(用户确认):
- **频次定级**保留(快讯/小时级/日级/周级/双周级/月级),第 5 步标题保留"频次定级"。
- **风险词**(R 层关键词、负面新闻轨的 AND 条件)保留——它是检索手段,不是风险等级判断。
- **相关度** direct/indirect/context 保留。

## 2. 术语澄清

- **风险等级**:每轨的 critical/high/medium/low 标签,判断"这组监测风险大不大"。本次删除对象。
- **风险词**:关键词字典 R 层负面词(strike/protest/lawsuit 等),作为"负面新闻"轨的 AND 检索条件。是检索手段,保留。

## 3. 改动一:轨 key 语义化

现:6 类轨 key `a/b/c/快讯/司法/招标`,任务 ID 形如 `Q0-a`。
改:6 类中文轨 key `全量新闻/负面新闻/行业新闻/快讯/司法/招标`,任务 ID 形如 `Q0-全量新闻`。

语义对应(检索式构成不变,仅命名):

| 新 key | 原 key | 构成 | 作用 |
|---|---|---|---|
| 全量新闻 | a | 实体键簇 + 地域限定 | 宽召回,掌握全貌 |
| 负面新闻 | b | 实体键簇 AND 风险词 | 只抓负面,可高频,预警主力 |
| 行业新闻 | c | 地名键 AND 行业词 AND 外资标识 | 抓未点名早期信号 |
| 快讯 | 快讯 | 不变 | 人员安全/应急管道 |
| 司法 | 司法 | 不变 | 诉讼/判例 |
| 招标 | 招标 | 不变 | 中标公告 |

改动点(轨 key 校验/定义 4 处):

1. `skills/overseas-sentiment-query-builder/scripts/_common.py:14` — `TRACK_KEYS = ("全量新闻", "负面新闻", "行业新闻", "快讯", "司法", "招标")`
2. `skills/overseas-sentiment-query-builder/scripts/step4_queries.py:58` — 校验失败提示文案同步
3. `graph/nodes.py:114` — step4 prompt 的 key 枚举说明同步
4. `graph/state.py:16` — `TRACK_KEYS` 注释同步

文档层轨名同步(LLM 按这些文档学轨 key,必须同步,否则会输出旧字母):

- `SKILL.md:77,81-83` — 分组说明"双轨三式"表:`a 全量轨`/`b 精准轨`/`c 不点名轨` → `全量新闻轨`/`负面新闻轨`/`行业新闻轨`(保留"双轨三式"概念名与"精准轨"等描述词,仅轨 key 前缀改中文)
- `references/query-patterns.md:23,29,35` — 三式小节标题同上
- `references/keyword-dictionary.md:49` — "不点名轨(c 式)" → "行业新闻轨"
- `assets/task_spec_example.json:7,21` — 注释 "a+b 两式"/"Q0b 集团层精准轨" → "全量+负面两式"/"Q0-负面新闻 集团层负面新闻轨"

## 4. 改动二:移除风险等级(全链路 10 处)

1. **Track 模型**:`graph/state.py:32` 删 `risk` 字段。
2. **step6 脚本**:`scripts/_common.py:16` 删 `RISKS` 常量;`scripts/step6_cadence.py` 删 risk 校验(norm_choice)与输出字段。
3. **格式契约**:`references/output-formats.md` 步骤 6 schema 删 `risk` 字段与枚举说明,步骤 6 标题改"频次与相关度定级"。
4. **节点 prompt**:`graph/nodes.py:123-126` step6 prompt 改"你是频次定级专家。按信号为每轨定频次与相关度",JSON 样例删 `"risk": "medium"`;写回 state(nodes.py:233-234)删 `tr["risk"] = ...`。
5. **导出转换**:`store/converter.py:43` 删 risk 键。
6. **Excel 生成**:`scripts/build_task_xlsx.py` 删"风险等级"列(RISK_FILL/RISK_FONT 色标 + TASK_HEADERS 表头 + TASK_WIDTHS 列宽),表头从 12 列变 11 列。
7. **SKILL.md**:第 5 步标题"定频次与风险等级"→"频次定级";Sheet 1 列定义删"风险等级";调度说明 sheet 删"频次与风险等级用色标区分优先级"句(保留频次色标句)。
8. **频次定级参考**:`references/cadence-and-risk.md` 删"风险等级四档"章节;频次五档表删引用 risk 的措辞(如"critical 且涉人员安全"→"涉人员安全");快讯管道小节同步;保留提频/降频信号、承包关系核实、相关度分层。
9. **前端**:`web/demo.html` 删风险 tag 渲染(demo.html:76-77 风险 tag 样式、298 风险 tag span)、轨细节删 `t.risk`(demo.html:300)、步骤 6 标题"频次定级 · 快讯 / 日 / 周 · 风险等级"→"频次定级 · 快讯 / 日 / 周"(demo.html:170)。

10. **信源参考**:`references/source-whitelists.md:46` "是风险等级跃迁的信号" → "是事件升级的信号";`source-whitelists.md:54` "风险等级要相应上调" → "事件重要性要相应上调"。

附:模板 `assets/task_spec_example.json` 删 4 处 `"risk"` 字段(16/30/44/58 行)。

## 4b. 测试更新(新增)

受影响的测试,必须同步更新:

1. `tests/test_sentiment_query_agent.py:68-69` `test_step4_queries_tracks` — 轨 key 断言 `"a"`/`"b"` → `"全量新闻"`/`"负面新闻"`。
2. `tests/test_sentiment_query_agent.py:84` `test_step6_cadence_fix_fast` — 输入可保留 `risk`(验证忽略逻辑),断言不变;若改为不带 risk 输入更贴合新契约,则仅删 risk 键,断言不变。
3. `tests/test_sentiment_query_agent.py:121,124` `_sample_group` — 轨 key `"a"`/`"b"` → 中文;删 `risk` 字段;`test_converter_selected_only` 断言任务不含 risk。
4. `step4_queries.py:40` — 标准化输出轨对象里的 `"risk": ""` 占位字段删掉。

## 5. 错误处理

- LLM 若仍输出 `risk` 字段:step6_cadence.py 只取 frequency/relevance 输出,多余 risk 直接忽略——不报错、不记 GAP,向后兼容,不阻塞流水线。
- step4 轨 key 校验:LLM 输出旧字母 key(a/b/c)→ 不在新 TRACK_KEYS 内 → 记 GAP 跳过(现行为),提示文案引导用中文 key。

## 6. 数据流(不变,仅少一字段)

```
step4 建轨(全量/负面/行业/快讯/司法/招标) → step5 补信源 → step6 补频次+相关度
converter 勾选 → spec tasks(无 risk) → build_task_xlsx 11 列 Excel
```

## 7. 测试

1. `pytest tests/test_sentiment_query_agent.py` 跑现有 12 测试,修受影响的:step6_cadence 单测(risk 断言)、converter 单测、_common 单测。
2. 手动:`echo 样例 | python3 scripts/step4_queries.py` 确认中文轨 key 通过校验;`echo 样例 | python3 scripts/step6_cadence.py` 确认 risk 被忽略、快讯轨强制频次仍生效。
3. 端到端:demo 页跑全流程,确认轨名中文 + Excel 无"风险等级"列。

## 8. 旧数据与兼容

- 已 commit 的方案组不迁移,保持原格式(旧轨 key a/b/c,含 risk)。导出仍按旧格式出 `Q0-a`。
- 新格式需重新生成方案。demo 阶段无真实存量,影响可忽略。

## 9. 不做(明确排除)

- 不删风险词(R 层词表、负面新闻轨 AND 条件)。
- 不删频次定级。
- 不删相关度 direct/indirect/context。
- 不改 `cadence-and-risk.md` 文件名(内部文件名,非输出)。
