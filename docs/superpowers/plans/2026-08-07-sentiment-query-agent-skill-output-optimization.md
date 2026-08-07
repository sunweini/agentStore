# sentiment-query-agent 轨 key 语义化 + 移除风险等级 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 sentiment-query-agent 的轨 key 从字母代号(a/b/c)改为中文语义名(全量新闻/负面新闻/行业新闻),并从全链路移除风险等级(critical/high/medium/low)。

**Architecture:** 契约先行——先改分步脚本的共享常量与校验(`_common.py`/`step4`/`step6`),再改消费方(agent 代码层 state/nodes/converter),再改生成物(Excel 脚本、skill 文档、前端),最后端到端验证。轨 key 语义化与风险等级移除交叉散布在 14 个文件,每个文件内改动点已在任务中逐条列出。

**Tech Stack:** Python 3.12 + pytest + openpyxl + 静态文本编辑(markdown/json/html)。

## Global Constraints

- 轨 key 固定 6 类(全链路唯一枚举):`全量新闻 / 负面新闻 / 行业新闻 / 快讯 / 司法 / 招标`。
- 每轨字段集(无 risk):`key / boolean_query / google_query / sources / frequency / relevance / selected`。
- **保留**:风险词术语(R 层词表、负面新闻轨的 AND 条件)、频次定级(快讯/小时级|日级|周级|双周级|月级)、相关度(direct|indirect|context)。
- LLM 若仍输出 `risk` 字段:step6_cadence.py 忽略之——不报错、不记 GAP。
- 旧字母轨 key(a/b/c)校验失败:记 GAP 跳过(现行为),不迁移旧数据。
- 输出产物(Excel 表头、demo 页面)不得含"风险等级"字样;"风险词"字样允许保留。
- skill 分步脚本契约:读 stdin JSON → 校验/标准化/记 GAP00N → 写 stdout JSON,非法输入退出码非 0 + stderr `FORMAT_ERROR`。
- 收尾必须更新 `CHANGELOG.md`(dev-standards §4),测试通过后 commit。

---

### Task 1: 契约常量 + step4 脚本(轨 key 语义化)

**Files:**
- Modify: `agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/scripts/_common.py:14`
- Modify: `agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/scripts/step4_queries.py:39-43,58`
- Test: `tests/test_sentiment_query_agent.py`(test_step4_queries_tracks 附近,约 63-75 行)

**Interfaces:**
- Consumes: 现有 `TRACK_KEYS` 元组、`step4_queries.py` 校验逻辑。
- Produces: `TRACK_KEYS = ("全量新闻", "负面新闻", "行业新闻", "快讯", "司法", "招标")`;step4 输出轨对象**不含** `risk` 占位字段。后续 Task 2-6 全部依赖此枚举。

- [ ] **Step 1: 更新测试(中文轨 key + 旧字母拒绝)**

把 `test_step4_queries_tracks` 的轨 key 改为中文,加 `"risk" not in` 断言,并新增一个旧字母 key 拒绝测试。替换现有 `test_step4_queries_tracks` 函数体:

```python
def test_step4_queries_tracks():
    """轨 key 用中文语义名:全量新闻/负面新闻/行业新闻/快讯/司法/招标。"""
    out = _run_script("step4_queries.py", {"schemes": [
        {"id": "Q0", "name": "集团层", "tracks": [
            {"key": "全量新闻", "boolean": "(A)", "google": "(A)"},
            {"key": "负面新闻", "boolean": "(A) AND (strike)", "google": "(A) strike"},
        ]},
    ]})
    sc = out["schemes"][0]
    assert sc["tracks"][0]["boolean_query"] == "(A)"
    assert sc["tracks"][1]["key"] == "负面新闻"
    # 每轨默认 selected=True,sources 空列表,且不含 risk 字段
    assert sc["tracks"][0]["selected"] is True
    assert sc["tracks"][0]["sources"] == []
    assert "risk" not in sc["tracks"][0]


def test_step4_rejects_old_letter_keys():
    """旧字母轨 key(a/b/c)不在新 TRACK_KEYS:全部无效 → 脚本非 0 退出。"""
    import json
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "step4_queries.py")],
        input=json.dumps({"schemes": [{"id": "Q0", "name": "集团层", "tracks": [
            {"key": "a", "boolean": "(A)", "google": "(A)"},
        ]}]}),
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0
    assert "FORMAT_ERROR" in proc.stderr
```

(若文件头部已 import json/subprocess,去掉函数内重复 import。)

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_sentiment_query_agent.py::test_step4_queries_tracks tests/test_sentiment_query_agent.py::test_step4_rejects_old_letter_keys -v`
Expected: 两个都 FAIL——中文 key 不在 TRACK_KEYS 触发 fail/跳过,旧字母 key 反而通过(断言 `!= 0` 失败)。

- [ ] **Step 3: 改 `_common.py` TRACK_KEYS**

`agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/scripts/_common.py:14`:

```python
TRACK_KEYS = ("全量新闻", "负面新闻", "行业新闻", "快讯", "司法", "招标")
```

- [ ] **Step 4: 改 `step4_queries.py`(删 risk 占位 + 文案)**

`step4_queries.py:39-43` 删 `"risk": "",` 行(轨对象占位字段):

```python
            normed_tracks.append({
                "key": key,
                "boolean_query": boolean,
                "google_query": google,
                "sources": [],
                "frequency": "",
                "relevance": "",
                "selected": True,
            })
```

`step4_queries.py:58` 校验失败文案:

```python
    if not normed:
        fail("schemes 为空或全部无有效轨:LLM 未按格式输出轨(检查 key 是否在 全量新闻/负面新闻/行业新闻/快讯/司法/招标)")
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_sentiment_query_agent.py::test_step4_queries_tracks tests/test_sentiment_query_agent.py::test_step4_rejects_old_letter_keys -v`
Expected: 两个都 PASS。

- [ ] **Step 6: Commit**

```bash
git add agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/scripts/_common.py agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/scripts/step4_queries.py tests/test_sentiment_query_agent.py
git commit -m "feat: 轨 key 语义化(全量新闻/负面新闻/行业新闻),step4 删 risk 占位"
```

---

### Task 2: step6 脚本删 risk(_common 删 RISKS)

**Files:**
- Modify: `agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/scripts/_common.py:16`
- Modify: `agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/scripts/step6_cadence.py:11,28-39`
- Test: `tests/test_sentiment_query_agent.py::test_step6_cadence_fix_fast`(约 81-88 行)

**Interfaces:**
- Consumes: Task 1 的新 `TRACK_KEYS`;现有 `FREQUENCIES`/`RELEVANCES`。
- Produces: `_common.py` 无 `RISKS` 常量;step6 输出轨对象为 `{"key", "frequency", "relevance"}`(无 risk)。Task 3 的 nodes.py 写回逻辑按此消费。

- [ ] **Step 1: 更新测试(输入保留 risk 验证忽略,断言输出无 risk)**

替换 `test_step6_cadence_fix_fast`:

```python
def test_step6_cadence_fix_fast():
    """快讯轨强制快讯/小时级;多余 risk 字段被忽略,输出不含 risk。"""
    out = _run_script("step6_cadence.py", {"schemes": [
        {"id": "Q3", "tracks": [{"key": "快讯", "frequency": "周级", "risk": "critical"}]},
    ]})
    tr = out["schemes"][0]["tracks"][0]
    assert tr["frequency"] == "快讯/小时级"
    assert "risk" not in tr
    assert any("GAP" in g for g in out.get("_gaps", []))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_sentiment_query_agent.py::test_step6_cadence_fix_fast -v`
Expected: FAIL——当前输出含 `risk` 字段,`"risk" not in tr` 断言不成立。

- [ ] **Step 3: 删 `_common.py` RISKS 常量**

`_common.py:16` 整行删除:

```python
RISKS = ("critical", "high", "medium", "low")
```

- [ ] **Step 4: 改 `step6_cadence.py`**

`step6_cadence.py:11` import 去掉 RISKS:

```python
from _common import FREQUENCIES, RELEVANCES, emit, gap, load_input, norm_choice, norm_list, norm_str, with_gaps  # noqa: E402
```

`step6_cadence.py:30-39` 删 risk 校验与输出字段,注释去"critical":

```python
            freq = norm_choice(tr.get("frequency"), f"schemes[{i}].tracks[{j}].frequency",
                               FREQUENCIES, "周级")
            rel = norm_choice(tr.get("relevance"), f"schemes[{i}].tracks[{j}].relevance",
                              RELEVANCES, "direct")
            # 快讯轨强制快讯/小时级
            if tr.get("key") == "快讯" and freq != "快讯/小时级":
                gap(f"快讯轨 {sc.get('id', i)} 频次应为快讯/小时级,已纠正")
                freq = "快讯/小时级"
            normed_tracks.append({"key": tr.get("key", ""), "frequency": freq,
                                  "relevance": rel})
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_sentiment_query_agent.py::test_step6_cadence_fix_fast -v`
Expected: PASS(快讯轨强制频次仍生效,risk 被忽略)。

- [ ] **Step 6: Commit**

```bash
git add agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/scripts/_common.py agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/scripts/step6_cadence.py tests/test_sentiment_query_agent.py
git commit -m "feat: step6 移除 risk 字段(忽略 LLM 多余输入),删 RISKS 常量"
```

---

### Task 3: agent 代码层(state / nodes / converter)

**Files:**
- Modify: `agents/sentiment_query_agent/graph/state.py:15-16,32`
- Modify: `agents/sentiment_query_agent/graph/nodes.py:112-118,123-126,233-234`
- Modify: `agents/sentiment_query_agent/store/converter.py:43`
- Test: `tests/test_sentiment_query_agent.py`(_sample_group 约 118-130 行、test_converter_selected_only)

**Interfaces:**
- Consumes: Task 1/2 的脚本契约(轨 key 中文、track 无 risk)。
- Produces: `Track` TypedDict 无 risk;nodes step6 prompt 与写回无 risk;converter `group_to_spec` 的 task 行无 risk 键。Task 4 的 build_task_xlsx 按 11 字段 task 行消费。

- [ ] **Step 1: 更新测试(_sample_group 中文轨 key + 删 risk,converter 断言)**

`_sample_group` 的 tracks 改为:

```python
             "tracks": [
                 {"key": "全量新闻", "boolean_query": "(A)", "google_query": "(A)",
                  "sources": ["media.com"], "frequency": "周级",
                  "relevance": "direct", "selected": True},
                 {"key": "负面新闻", "boolean_query": "(A) AND (strike)", "google_query": "(A) strike",
                  "sources": [], "frequency": "日级",
                  "relevance": "direct", "selected": False},
             ]},
```

`test_converter_selected_only` 在 `assert spec["tasks"][0]["sources"] == ["media.com"]` 后加一行:

```python
    assert "risk" not in spec["tasks"][0]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_sentiment_query_agent.py::test_converter_selected_only -v`
Expected: FAIL——converter 仍输出 `risk` 键(读缺失字段补默认 "medium"),`"risk" not in spec["tasks"][0]` 不成立。

- [ ] **Step 3: 改 `state.py`(TRACK_KEYS 注释 + 删 risk 字段)**

`state.py:15-16`:

```python
# 轨类型固定 6 类(与 skill references/output-formats.md 对齐)
TRACK_KEYS = ("全量新闻", "负面新闻", "行业新闻", "快讯", "司法", "招标")
```

`state.py:32` 删整行:

```python
    risk: str                # critical/high/medium/low
```

- [ ] **Step 4: 改 `nodes.py`(step4/step6 prompt + 写回)**

`nodes.py:114` step4 prompt key 枚举行:

```python
       "重要:每轨的 key 字段只允许这 6 个值之一: 全量新闻 负面新闻 行业新闻 快讯 司法 招标。"
```

`nodes.py:117-118` step4 prompt JSON 样例(轨 key 改中文):

```python
       '{{"schemes": [{{"id": "Q0", "name": "集团层", "region": "全语种", "lang": "中/英", '
       '"desc": "", "gaps": [], "tracks": [{{"key": "全量新闻", "boolean": "(...)", "google": "(...)"}}]}}]}}',
```

`nodes.py:123-126` step6 prompt(标题、样例删 risk、key 改中文):

```python
    6: "你是频次定级专家。按信号为每轨定频次与相关度。先 websearch 验证时效,再输出 JSON。\n"
       "输出格式(JSON,schemes 结构与步骤 4 对应):\n"
       '{{"schemes": [{{"id": "Q0", "tracks": [{{"key": "全量新闻", "frequency": "周级", '
       '"relevance": "direct"}}]}}]}}',
```

`nodes.py:233-234` 写回删 risk 行:

```python
                        tr["frequency"] = meta.get("frequency", "周级")
                        tr["relevance"] = meta.get("relevance", "direct")
```

- [ ] **Step 5: 改 `converter.py` 删 risk 键**

`converter.py:43` 删整行:

```python
                "risk": tr.get("risk", "medium"),
```

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/test_sentiment_query_agent.py -v`
Expected: 全部 PASS(converter 测试 + step4/step6 测试)。

- [ ] **Step 7: Commit**

```bash
git add agents/sentiment_query_agent/graph/state.py agents/sentiment_query_agent/graph/nodes.py agents/sentiment_query_agent/store/converter.py tests/test_sentiment_query_agent.py
git commit -m "feat: agent 代码层移除 risk(state/nodes/converter),step prompt 轨 key 语义化"
```

---

### Task 4: Excel 生成脚本删"风险等级"列

**Files:**
- Modify: `agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/scripts/build_task_xlsx.py:41-45,47-51,90,98-107`

**Interfaces:**
- Consumes: Task 3 的 11 字段 task 行(无 risk)。
- Produces: 检索任务清单 sheet 11 列(任务ID/检索组/国家地区/语种/布尔/Google/白名单/频次/相关度/状态/运营注),auto_filter `A1:K`。Task 7 端到端按此验证。

- [ ] **Step 1: 删 RISK 色标与表头**

`build_task_xlsx.py:41-45` 删整块:

```python
RISK_FILL = {
    "critical": PatternFill("solid", fgColor="FF4136"),
    "high": PatternFill("solid", fgColor="FFDC00"),
}
RISK_FONT = {"critical": Font(name="Arial", size=10, bold=True, color="FFFFFF")}
```

`build_task_xlsx.py:47-51` 表头与列宽(12→11 列):

```python
TASK_HEADERS = [
    "任务ID", "检索组", "国家/地区", "语种", "检索式(布尔)", "检索式(Google语法)",
    "目标信源白名单(域名)", "建议频次", "命中期望相关度", "状态", "运营注/说明",
]
TASK_WIDTHS = [9, 20, 14, 8, 60, 50, 46, 14, 14, 8, 50]
```

- [ ] **Step 2: 删数据行与色标逻辑**

`build_task_xlsx.py:90` 数据行删 risk 取值:

```python
            t.get("frequency", ""), t.get("relevance", ""),
```

`build_task_xlsx.py:98-106` 删 risk 色标,保留频次色标:

```python
        freq = t.get("frequency", "")
        if freq in FREQ_FILL:
            ws.cell(ri, 8).fill = FREQ_FILL[freq]
        if freq in FREQ_FONT:
            ws.cell(ri, 8).font = FREQ_FONT[freq]
```

`build_task_xlsx.py:107` auto_filter 12→11 列:

```python
    ws.auto_filter.ref = f"A1:K{len(tasks) + 1}"
```

- [ ] **Step 3: 冒烟验证(生成 Excel 断言表头)**

Run(项目根目录):

```bash
python3 - <<'EOF'
import json, subprocess, openpyxl
spec = {"title": "冒烟", "tasks": [{"id": "Q0-全量新闻", "group": "集团层", "region": "全语种",
  "lang": "zh", "boolean": "(A)", "google": "(A)", "sources": [], "frequency": "周级",
  "relevance": "direct", "status": "待启用", "note": ""}], "keywords": [], "extra_notes": []}
open("/tmp/smoke_spec.json", "w").write(json.dumps(spec, ensure_ascii=False))
subprocess.run(["python3", "agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/scripts/build_task_xlsx.py",
                "/tmp/smoke_spec.json", "/tmp/smoke.xlsx"], check=True)
wb = openpyxl.load_workbook("/tmp/smoke.xlsx")
hdr = [c.value for c in wb["检索任务清单"][1]]
assert "风险等级" not in hdr, hdr
assert len(hdr) == 11, hdr
print("OK:", hdr)
EOF
```

Expected: `OK: ['任务ID', '检索组', '国家/地区', '语种', '检索式(布尔)', '检索式(Google语法)', '目标信源白名单(域名)', '建议频次', '命中期望相关度', '状态', '运营注/说明']`。

- [ ] **Step 4: Commit**

```bash
git add agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/scripts/build_task_xlsx.py
git commit -m "feat: Excel 检索任务清单删风险等级列(12→11 列)"
```

---

### Task 5: skill 文档层同步(7 文件)

**Files:**
- Modify: `agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/SKILL.md`
- Modify: `agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/references/output-formats.md`
- Modify: `agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/references/cadence-and-risk.md`
- Modify: `agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/references/source-whitelists.md`
- Modify: `agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/references/query-patterns.md`
- Modify: `agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/references/keyword-dictionary.md`
- Modify: `agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/assets/task_spec_example.json`

**Interfaces:**
- Consumes: Task 1 的轨 key 枚举、Task 2 的 step6 契约(无 risk)。
- Produces: LLM 学习方法论的知识层文档与代码契约一致。Task 7 用 grep 验证无残留。

- [ ] **Step 1: 改 `SKILL.md`**

逐条替换(按出现顺序):

1. line 3 description: `频次与风险定级` → `频次定级`
2. line 81-83 双轨表轨名:
   - `| **a 全量轨** |` → `| **全量新闻轨** |`
   - `| **b 精准轨** |` → `| **负面新闻轨** |`
   - `| **c 不点名轨** |` → `| **行业新闻轨** |`
3. line 107: `### 第 5 步：定频次与风险等级` → `### 第 5 步：频次定级`
4. line 116: `| 双周/月级 | 轻扫组、低风险区域 |` → `| 双周/月级 | 轻扫组、待证项目区域 |`
5. line 120: `每轨补 frequency/risk/relevance` → `每轨补 frequency/relevance`
6. line 134: `频次与风险的色标` → `频次色标`
7. line 177: `step6 补频次风险` → `step6 补频次相关度`
8. line 191 列定义: `| 目标信源白名单(域名) | 建议频次 | 风险等级 | 命中期望相关度 | 状态 | 运营注/说明` → 删 `风险等级 |`
9. line 194: `频次与风险等级用色标区分优先级` → `频次用色标区分优先级`
10. line 223: `频次与风险定级规则` → `频次定级规则`

- [ ] **Step 2: 改 `output-formats.md`(格式契约)**

1. line 81: `每轨补 frequency/risk/relevance,组装完整 task 行。` → `每轨补 frequency/relevance,组装完整 task 行。`
2. line 86-88 步骤 6 JSON 样例:

```json
{
  "schemes": [
    {"id": "Q0",
     "tracks": [{"key": "全量新闻", "frequency": "周级", "relevance": "direct"}]}
  ]
}
```

3. line 92: `frequency:快讯/小时级|日级|周级|双周级|月级;risk:critical|high|medium|low;relevance:direct|indirect|context。` → `frequency:快讯/小时级|日级|周级|双周级|月级;relevance:direct|indirect|context。`
4. line 79 标题 `## 步骤 6 频次定级 → step6_cadence.py` 保持(已无 risk 字样)。

- [ ] **Step 3: 改 `cadence-and-risk.md`(删等级章节)**

1. line 1: `# 频次与风险定级` → `# 频次定级`
2. line 5: `频次和风险等级要能说出依据` → `频次要能说出依据`
3. line 9-18 删整节(`## 风险等级四档` 到 `预警的意义在于出事之前。` 之前的空行),保留后文"## 频次五档"。
4. line 26: `| **快讯/小时级** | critical 且涉人员安全的组。命中即推送,不等轮询 |` → `| **快讯/小时级** | 涉人员安全的组。命中即推送,不等轮询 |`
5. line 28: `| **周级** | 常规核心项目组（有实质业务、风险中等） |` → `| **周级** | 常规核心项目组（有实质业务） |`
6. line 30: `| **月级** | 轻扫组、待证项目、低风险区域 |` → `| **月级** | 轻扫组、待证项目区域 |`
7. line 34: `critical 涉人员安全的组，**不要混在常规轮询里**。` → `涉人员安全的组，**不要混在常规轮询里**。`

- [ ] **Step 4: 改 `source-whitelists.md`(2 处措辞)**

1. line 46: `是风险等级跃迁的信号。` → `是事件升级的信号。`
2. line 54: `风险等级要相应上调。` → `事件重要性要相应上调。`

- [ ] **Step 5: 改 `query-patterns.md`(轨名 + 低风险措辞)**

1. line 7: `低风险轻扫组月级就够` → `轻扫组月级就够`
2. line 15: `把所有低风险、待证、边缘项目打包低频跑` → `把所有待证、边缘项目打包低频跑`
3. line 23: `### a 全量轨` → `### 全量新闻轨`
4. line 29: `### b 精准轨` → `### 负面新闻轨`
5. line 35: `### c 不点名轨` → `### 行业新闻轨`
6. line 45: `高风险区域组三式齐全` → `重点区域组三式齐全`
7. line 46: `集团层通常只要 a + b` → `集团层通常只要全量+负面两式`;`轻扫组一条 a 式即可` → `轻扫组一条全量式即可`

- [ ] **Step 6: 改 `keyword-dictionary.md`**

1. line 49: `不点名轨（c 式）完全依赖这一层。` → `行业新闻轨完全依赖这一层。`

(其余"风险词""精准轨"等为保留术语,不动。)

- [ ] **Step 7: 改 `task_spec_example.json`(删 risk + 注释与 id 语义化)**

1. 删 4 处 `"risk"` 字段行(16/30/44/58 行):`"risk": "medium",`、`"risk": "high",`、`"risk": "high",`、`"risk": "critical",`
2. line 7 注释: `"Q0 集团层全量轨。集团层通常只需 a+b 两式，不需要不点名轨。"` → `"Q0-全量新闻 集团层全量新闻轨。集团层通常只需全量+负面两式，不需要行业新闻轨。"`
3. line 8 id: `"id": "Q0",` → `"id": "Q0-全量新闻",`
4. line 21 注释: `"Q0b 集团层精准轨：实体键 AND 风险词，只抓负面，可高频。"` → `"Q0-负面新闻 集团层负面新闻轨：实体键 AND 风险词，只抓负面，可高频。"`
5. line 22 id: `"id": "Q0b",` → `"id": "Q0-负面新闻",`
6. line 35 注释: `"区域组不点名轨示例：..."` → `"区域组行业新闻轨示例：..."`
7. line 36 id: `"id": "Q1c",` → `"id": "Q1-行业新闻",`
8. line 49 注释: `"人员安全级示例：涉战乱/治安，走快讯管道。"` → `"快讯轨示例：涉战乱/治安，走快讯管道。"`
9. line 50 id: `"id": "Q6",` → `"id": "Q6-快讯",`

- [ ] **Step 8: 验证无残留**

Run(项目根目录):

```bash
grep -rn "风险等级\|critical\|RISK\|a 全量轨\|b 精准轨\|c 不点名轨" agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/ --include="*.md" --include="*.json"
```

Expected: 无输出(cadence-and-risk.md 文件名含 risk 属文件名,不在内容中;grep 检查的是内容)。

- [ ] **Step 9: Commit**

```bash
git add agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/
git commit -m "docs: skill 文档层同步(轨名语义化 + 移除风险等级措辞/格式契约)"
```

---

### Task 6: 前端 demo.html

**Files:**
- Modify: `web/demo.html:76-78,170,298,300`

**Interfaces:**
- Consumes: Task 3 的 API 返回(轨 key 中文、track 无 risk)。
- Produces: 页面无风险等级展示,轨 key 直接显示中文(如 `全量新闻`),任务详情为 `频次/相关度`。

- [ ] **Step 1: 删风险 tag 样式**

`demo.html:76-78` 删整块:

```css
.tag.risk-critical{background:var(--err-bg);color:var(--err)}
.tag.risk-high{background:var(--warn-bg);color:var(--warn)}
.tag.risk-medium{background:#F0F1F4;color:var(--t2)}
```

- [ ] **Step 2: 删步骤 6 副标题风险字样**

`demo.html:170`:

```javascript
  ["频次定级", "快讯 / 日 / 周"],
```

- [ ] **Step 3: 删方案级风险 tag**

`demo.html:298`:

```javascript
      `<span class="tag freq">${sc.frequency || ""}</span>`;
```

- [ ] **Step 4: 删轨细节 risk**

`demo.html:300`:

```javascript
      `<label class="trk on" data-j="${j}"><span class="tb"></span><span><b>${t.key}</b> <span class="tkd">${t.frequency || ""}</span></span></label>`).join("");
```

- [ ] **Step 5: 验证无残留**

Run(项目根目录):

```bash
grep -n "risk\|风险等级" web/demo.html
```

Expected: 无输出。

- [ ] **Step 6: Commit**

```bash
git add web/demo.html
git commit -m "feat: demo 页移除风险等级展示,轨 key 直显中文"
```

---

### Task 7: 端到端验证 + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: Task 1-6 全部产出。
- Produces: 全量测试通过 + CHANGELOG 新版本条目。

- [ ] **Step 1: 全量测试**

Run: `pytest tests/test_sentiment_query_agent.py -v`
Expected: 全部 PASS(现有 12 测试 + 新增 `test_step4_rejects_old_letter_keys`)。

- [ ] **Step 2: 脚本冒烟(step4/step6 中文轨 key)**

Run:

```bash
echo '{"schemes": [{"id": "Q0", "tracks": [{"key": "全量新闻", "boolean": "(A)", "google": "(A)"}]}]}' | python3 agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/scripts/step4_queries.py
echo '{"schemes": [{"id": "Q0", "tracks": [{"key": "负面新闻", "frequency": "日级", "risk": "high", "relevance": "direct"}]}]}' | python3 agents/sentiment_query_agent/skills/overseas-sentiment-query-builder/scripts/step6_cadence.py
```

Expected: step4 输出中文 key 轨,无 risk 字段;step6 输出无 risk,risk 输入被忽略。

- [ ] **Step 3: 全局残留检查**

Run(项目根目录):

```bash
grep -rn "risk" agents/sentiment_query_agent/ web/ --include="*.py" --include="*.html" --include="*.json" --include="*.md" | grep -v __pycache__ | grep -v "风险词"
```

Expected: 无输出(cadence-and-risk.md 为文件名;grep 查内容)。

- [ ] **Step 4: 更新 CHANGELOG.md**

读 `CHANGELOG.md` 顶部,按现有格式追加新版本条目(当前最新 v1.1.0,新版本为 v1.2.0),内容:

```markdown
## [v1.2.0] - 2026-08-07

### 变更
- sentiment-query-agent:轨 key 语义化(a/b/c → 全量新闻/负面新闻/行业新闻),任务 ID 形如 Q0-全量新闻
- sentiment-query-agent:全链路移除风险等级(critical/high/medium/low):step6/state/nodes/converter/Excel/skill 文档/demo
- 保留:风险词(R 层词表、负面新闻轨 AND 条件)、频次定级、相关度 direct/indirect/context
- 兼容:LLM 多余 risk 输入被 step6 忽略;旧字母轨 key 校验失败记 GAP
```

(以 CHANGELOG.md 实际格式为准微调。)

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: CHANGELOG v1.2.0(轨 key 语义化 + 移除风险等级)"
```

- [ ] **Step 6(可选):demo 端到端**

若 gateway MCP 与 API key 环境可用:`uvicorn agents.sentiment_query_agent.api:app --reload` 起服务,`web/demo.html` 跑一次全流程,确认方案/轨名中文显示、导出 Excel 无"风险等级"列。环境不可用则跳过并在交付说明中注明。
