# 分步输出格式契约(6 步 schema)

本文件是 6 步流水线每步输出的**唯一格式定义**。节点 prompt 引用本文件,LLM 输出 JSON 后由对应 stepN.py 脚本校验/标准化。

字段设计对齐最终 Excel spec(assets/task_spec_example.json):步骤 3 = keywords 行,步骤 4+5+6 合并 = tasks 行,拼图式组装,导出零转换。

## 步骤 1 实体测绘 → step1_entities.py

```json
{
  "entities": {
    "parent": "母公司名",
    "subsidiaries": ["子公司1"],
    "overseas_entities": [{"name": "海外法人名", "lang": "en", "region": "赞比亚"}],
    "spelling_variants": ["拼写变体"],
    "interference_sources": ["同名干扰源1"]
  }
}
```

## 步骤 2 主体画像 → step2_profile.py

```json
{
  "profile": {
    "role": "承包商|业主|ai判定",
    "relevance_rules": {
      "direct": "点名监测主体自身或其在场承包的项目",
      "indirect": "未点名但可确定指向关联实体",
      "context": "行业性报道,未指向具体公司"
    },
    "regions": ["重点地区1"]
  }
}
```

## 步骤 3 关键词字典 → step3_keywords.py

对齐 spec `keywords[]` 行(6 列:层/键类别/关键词/语种/guard/备注)。

```json
{
  "keywords": [
    {"layer": "A", "category": "A1集团/公司名称簇", "terms": "\"中文全称\" \"ABBR\"", "lang": "全", "guard": "", "note": ""}
  ]
}
```

层固定 A/B/C/D/R/X。≤5 字符缩写必须填 guard(context_guard)。

## 步骤 4 双轨检索式 → step4_queries.py

对齐 spec `tasks[]` 行(部分:布尔+Google 双语法)。

```json
{
  "schemes": [
    {"id": "Q0", "name": "集团层·全量新闻", "region": "全语种", "lang": "中/英",
     "tracks": [{"key": "全量新闻", "boolean": "(...)", "google": "(...)"}]}
  ]
}
```

轨 key 固定 6 类:全量新闻 / 负面新闻 / 行业新闻 / 快讯 / 司法 / 招标。每式布尔+Google 双版本,语法差异见 query-patterns.md。

## 步骤 5 属地信源 → step5_sources.py

每轨补 `sources`(域名白名单),与步骤 4 的 schemes 结构一一对应。

```json
{
  "schemes": [
    {"id": "Q0",
     "tracks": [{"key": "全量新闻", "sources": ["属地媒体.com", "判例库.org"]}]}
  ]
}
```

## 步骤 6 频次定级 → step6_cadence.py

每轨补 frequency/relevance,组装完整 task 行。

```json
{
  "schemes": [
    {"id": "Q0",
     "tracks": [{"key": "全量新闻", "frequency": "周级", "relevance": "direct"}]}
  ]
}
```

frequency:快讯/小时级|日级|周级|双周级|月级;relevance:direct|indirect|context。

## 脚本职责

每个 stepN.py:读 stdin JSON(LLM 原始输出)→ 校验字段/标准化/补默认值 → 缺字段记 GAP → 输出 stdout JSON(本格式)。LLM 输出非 JSON → 退出码非 0 + stderr 说明。
