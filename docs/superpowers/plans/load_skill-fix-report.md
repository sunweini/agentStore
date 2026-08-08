# load_skill 机制接入 kingdee-plugin-agent — 实现报告

日期:2026-08-08 · 分支:main(HEAD 70b46d7) · 提交:`feat(skills): load_skill 机制(requirement-clarify 渐进式披露,对照 sentiment 模式)`

## 背景与目标

为 kingdee-plugin-agent 接入与 sentiment-query-agent 一致的 load_skill 渐进式披露机制:
摘要启动加载 → LLM 需要时主动调 `load_skill` 取完整方法论(SKILL.md + 模板)→ 2 回合上限。
本 agent 的 requirement-clarify skill 无 `references/` 子目录,类型模板(bill/service/list.md)
直接放 skill 目录,load_skill 的 references 字段即这些模板。

## 已实现(对照 sentiment 模式逐项)

### 1. skills/loader.py(新增,对照 agents/sentiment_query_agent/skills/loader.py)

- `_AVAILABLE_SKILLS = {"requirement-clarify": ...}` — 摘要按实际模板调整
  (单据-触发操作/校验字段/拦截方式/联动单据/异常处理;服务-入口/事务边界/异常回滚;
  列表-字段/按钮/过滤;一次一问、多选优先、10 轮上限、spec 决策+假设清单)。
- `@tool load_skill(skill_name)` — 返回 JSON {skill, summary, references, scripts, content},
  与 sentiment 形状一致;references 直接扫 skill 目录 `*.md`(剔除 SKILL.md),
  scripts 恒空列表(无分步脚本,保形状一致);未知 skill → error JSON + available 列表。
- `skill_summary()` — 摘要层 JSON(w1 澄清 prompt 注入)。
- `SKILL_HINT` — 每步注入的工具提示(对照 sentiment 方案 2a)。
- `structured_with_skill(llm, schema, messages)` — 统一绑定助手(见 §2)。
- 查找顺序:agents/kingdee_plugin_agent/skills/ → common/skills/(同 sentiment)。

### 2. worker 绑定(w1-w5,五个 LLM 调用点全绑定)

| worker | 调用点 | 绑定 |
|---|---|---|
| w1 RequirementWorker | generate_questions / split_subtasks | load_skill + `SKILL_HINT` + `skill_summary()` 注入澄清 prompt |
| w2 DesignWorker | _llm_design | load_skill + SKILL_HINT |
| w3 GenerateWorker | _llm_generate | load_skill + SKILL_HINT |
| w4 ReviewWorker | _llm_review | load_skill + SKILL_HINT |
| w5 CompileWorker | _llm_fix | load_skill + SKILL_HINT |

w5.5/w6/w7 无 LLM 调用点(确定性阶段),按任务范围跳过;supervisor 决策节点不在任务范围,未绑定。

**绑定形态(经安装包 introspection 实测核对,langchain MCP 不可用)**:
- `llm.bind_tools([load_skill]).with_structured_output(S)` 是**静默空操作** —— `bind_tools`
  返回 `_ChatModelBinding`,其 `with_structured_output` 经 `RunnableBinding.__getattr__`
  委派到被绑模型(只合并 config,不合并 bound kwargs),tools 被丢弃。
- 官方正确形态:`llm.with_structured_output(S, tools=[load_skill], include_raw=True)`
  —— langchain-openai 1.4.1 `BaseChatOpenAI.with_structured_output` json_schema 分支
  `if tools: bind_kwargs["tools"] = [convert_to_openai_tool(t, strict=strict) ...]` 实测源码核对。
  include_raw 返回 {raw, parsed, parsing_error};模型调工具时 parsed=None、raw 含 tool_calls。
- **不传 strict**:worker 输出 schema 含默认值字段(QuestionsOutput.questions 等),
  OpenAI strict json_schema 禁默认值,传 strict=True 会被 API 拒绝;load_skill 单字符串参数无需 strict。
- **2 回合上限**:回合 1 模型调 load_skill → helper 执行工具(`load_skill.invoke(tc["args"])`)
  → ToolMessage 喂回 → 回合 2 出 schema;parsed 仍空(再调工具/解析失败)→ None → worker
  既有确定性骨架降级(与 sentiment 工具循环语义一致)。
- **条件安全**:`hasattr(llm, "bind_tools")` 为 False 的脚本/fake LLM(ScriptedLLM 等)
  跳过绑定,走原 `with_structured_output(schema)` 路径 —— 146 个既有测试契约不变;
  with_structured_output 抛 TypeError(实现不支持 tools 参数)→ 回退普通路径。

### 3. 技能文档

- `skills/requirement-clarify/SKILL.md`(新增):核心规则(一次一问/多选优先/元数据驱动/
  10 轮上限/决策+假设记录/问题收敛)+ 三套类型模板引用表。
- `skills/__init__.py`(新增,空):对照 sentiment skills 目录包结构。

### 4. 测试(tests/test_kingdee_agent.py 新增 3 项)

- `test_load_skill_returns_requirement_clarify` — references 含 bill/list/service.md、
  content 含 SKILL.md 全文、scripts 空;未知 skill → error JSON + available。
- `test_skill_summary` — 摘要层含 requirement-clarify。
- `test_structured_with_skill_binds_tool_and_feeds_result_back` — RoundTripLLM(模拟真实
  模型:bind_tools + with_structured_output(tools=, include_raw=True)):工具回合 → 执行
  喂回(ToolMessage 内容含 SKILL.md 全文)→ schema 回合;恰好 2 回合;tools 参数真实下发。

### 5. 文档收尾(项目铁律)

- CHANGELOG.md:v1.5.0 条目(新增功能/测试)。
- agents/kingdee_plugin_agent/CLAUDE.md:文件表补 skills/loader.py 行 + 常用操作补改 skill 条目。

## 验证

- 基线(改动前):146 passed(124.68s)。
- 全量(改动后):`pytest tests/ -q` → **149 passed, 2 warnings**(92.01s)。
- 关键路径定向验证:test_graph_full_flow_to_finish(interrupt/resume 澄清全链路)、
  test_w1_split_subtasks_llm、新 3 项均绿。
- 踩坑实录:首次把 `skill_summary()`(含花括号的 JSON)直接拼进 ChatPromptTemplate
  system 文本 → "Nested replacement fields" 异常 → generate_questions 静默回落默认问题
  → 图测试第二轮 interrupt 变 confirm(KeyError 'round')。修复:摘要走模板变量占位
  `{skill_summary}`(dev-standards §7.2 f-string 陷阱,已在代码注释注明)。

## 变更文件

新增:
- agents/kingdee_plugin_agent/skills/loader.py
- agents/kingdee_plugin_agent/skills/__init__.py
- agents/kingdee_plugin_agent/skills/requirement-clarify/SKILL.md
- docs/superpowers/plans/load_skill-fix-report.md(本文件)

修改:
- agents/kingdee_plugin_agent/graph/workers/w1_requirement.py(w1:绑定 + 摘要注入)
- agents/kingdee_plugin_agent/graph/workers/w2_design.py / w3_generate.py / w4_review.py / w5_compile.py
- tests/test_kingdee_agent.py(+3 测试)
- CHANGELOG.md(v1.5.0)
- agents/kingdee_plugin_agent/CLAUDE.md

## 自审

- 形状对齐:load_skill 返回 JSON 与 sentiment 逐字段一致(skill/summary/references/scripts/content)。
- 降级安全:任何绑定失败(实现不支持/API 拒绝)→ None → worker 既有确定性骨架,
  与绑定前"LLM 故障 → 骨架"语义一致,不新增失败路径。
- 成本:真实模型每次调用多带一个 tools 定义(几十 token);模型不调工具时零额外往返。
- fake 兼容:ScriptedLLM 无 bind_tools → 原路径,146 项既有测试零改动全绿。

## 关注点 / 遗留

1. **sentiment loader 路径 bug(本项目既有,未动)**:`agents/sentiment_query_agent/skills/loader.py`
   的 `_AGENT_SKILLS` 用 `agents/sentiment-query-agent/skills`(连字符),实际目录是
   `sentiment_query_agent`(下划线)→ `_find_skill` 必返 None → sentiment 的 load_skill 实际
   返回 "目录缺失" error。kingdee 版用了正确目录名,未受影响。建议后续单独修复 sentiment loader。
2. **真实 DeepSeek 行为未实测**:structured_with_skill 的 include_raw/tools 路径用
   RoundTripLLM 模拟验证;真实 DeepSeek(经 ChatOpenAI,json_schema response_format)能否
   同时接受 tools + response_format 未经线上验证 —— 但失败即降级骨架,不新增中断路径
   (workers 绑定前对真实 DeepSeek 也从未在线验证,测试全走 ScriptedLLM 注入)。
3. **supervisor 未绑定 load_skill**:决策节点是确定性映射 + LLM 结构化裁决(有兜底),
   不在本次任务范围;后续若需方法论供给可同样接入。
4. **w2-w5 的 SKILL_HINT 内容通用**(金蝶插件方法论),requirement-clarify 是当前唯一 skill;
   后续新增 skill 时只需扩展 _AVAILABLE_SKILLS,无需改 worker。
