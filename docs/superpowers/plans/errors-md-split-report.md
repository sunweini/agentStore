# errors.md 纯方法论化重构报告(错误条目单一来源经验库)

日期:2026-08-08 · 分支:main · 提交:`refactor(skills): errors.md 纯方法论,错误条目单一来源经验库(动态)`

## 目标与结论

把 compile-fixer skill 的静态错误映射与动态经验库彻底分离:**errors.md 只含方法论,
所有具体"错误码 → 根因 → 修法"映射的单一来源是经验库**(启动种子 + w7 沉淀)。
修复 LLM 的上下文构成:编译错误 + 经验库命中(search_related 附注)+ 方法论(load_skill)。

## 改动内容

### 1. `agents/kingdee_plugin_agent/skills/compile-fixer/references/errors.md`(重写为纯方法论)

- **删除**:旧分类表的全部静态映射(A1 CS0246 补 using / A2 服务插件基类变体 /
  A3 CS0234 命名空间拼错 / B1 CS0506·CS0115 签名 / C1 CS0103 / C2 CS1061 /
  D1 CS1002·CS1525·CS1519 / D2 占位符残留 + "经验条目(seed)" 标注)。
- **保留并强化**:
  - 错误分类框架:分析维度 + "判断自问"(A 缺引用与命名空间 / B 签名与基类不匹配 /
    C 拼写与作用域 / D 语法与占位符残留),不再用错误码表驱动分类。
  - 根因分析方法:表象 vs 根因、级联错误找源头、签名错误是级联源、阻断性优先、
    不编造成员名、对照模板/规范。
  - 经验库检索策略:按错误码+消息语义 search_related、命中自核、verified 优先、
    未命中不拒绝修复。
  - 修复纪律:5 轮上限、禁止重复提交相同代码、修复后必重编、不删功能代码、
    服务不可用不计轮次。
- **显式声明**:文件头部 blockquote —— "具体错误映射见经验库(启动种子
  seed/compile_errors.json + w7 运行沉淀),本文件只含方法论;新踩坑不写这里,
  走 w7 沉淀进经验库(proposed → verified)"。
- **契约**:errors.md 全文件不再出现任何 `CS\d{4}` 错误码与"经验条目(seed)"标记。

### 2. `agents/kingdee_plugin_agent/skills/compile-fixer/SKILL.md`(同步措辞)

- 首段:load_skill 交付内容描述"本文与错误模式库"→"本文与 references/errors.md(纯方法论)"。
- 修复流程第 2 步:根因定位不再列举具体错误码(删 CS0506/CS0115),改为"按 errors.md
  根因分析方法;具体根因链与修法从 compile_errors 的 experience 附注取"。
- 踩坑纪律:级联错误示例 "CS1061 × N" → "成员不存在 × N"(skill 全文件零静态错误码)。
- 参考文件段:errors.md 描述改为"编译修复方法论(分类/根因/检索/纪律;具体错误映射在经验库)"。

### 3. 措辞同步(loader / prompt)

- `agents/kingdee_plugin_agent/skills/loader.py`:`compile-fixer` 摘要尾句改为
  "具体错误映射单一来源为经验库(启动种子 + w7 沉淀),skill 只含方法论"
  (`test_skill_summary` 的 "5 轮" 断言保持通过)。
- `agents/kingdee_plugin_agent/prompts/w5_compile.md`:"返回 SKILL.md + 常见编译错误
  模式库" → "方法论与检索指引;具体错误映射不查静态表,已随 experience 附注给出"。

### 4. 测试(`tests/test_kingdee_agent.py`)

- **更新** `test_load_skill_codegen_review_fixer_references`:compile-fixer 断言从
  "CS0246/CS1061/CS0506 在 errors.md" 改为方法论词(分类框架/根因分析/经验库)。
- **新增** `test_errors_md_pure_methodology_no_static_mappings`:契约测试 ——
  四件套方法论在;指向经验库单一来源(seed + w7、"新踩坑不写这里");`CS\d{4}` 正则
  零命中;"经验条目(seed)" 标记消失。
- **新增** `test_compile_experience_hits_reach_llm_fix_context`:捕获型 fake LLM
  (无 bind_tools,返回改写代码)跑完整 w5 循环,断言 5 轮每轮的 human 消息 context
  都含经验库命中文本("[CS0103] 名称不存在 …"),锁死"动态检索结果进修复 prompt"路径。

## w5 检索路径核验(动态-only,无需改代码)

`agents/kingdee_plugin_agent/graph/workers/w5_compile.py` 现状已满足"动态-only":

- `_retrieve_fix(subtask, errors)`:经验库故障兜底 try/except;每失败轮按
  `err.code + err.message` 调 `experience.search_related(k=2)`,命中文本附注到
  `compile_errors[i]["experience"]`(C8 骨架保留,C10 未改动、未退化)。
- `_llm_fix(subtask, code)`:把 `{"code", "compile_errors"}`(含 experience 附注)
  序列化进 human context;方法论经 `load_skill("compile-fixer")` 由 LLM 主动调用
  (`structured_with_skill` 2 回合绑定)。
- 即修复 LLM 看到的正是:**编译错误 + 经验库命中 + 方法论** —— 无任何静态错误表
  参与;本次重构只把 skill 内的静态映射删掉,检索路径本就是动态的。

## 种子与灌入核验

- `agents/kingdee_plugin_agent/seed/compile_errors.json`(5 条)未改动;
  `seed/seed_load.py` 未改动。`tests/test_rag.py::test_seed_load_idempotent`
  (种子文本/幂等/与 propose 格式统一)全量回归通过,灌入链路不受影响。
- errors.md 重写后与种子不再有内容耦合(旧 errors.md 的"经验条目(seed)"引用已删除,
  种子文本仍是经验库内的唯一静态基线)。

## 测试结果

- 全量:`158 passed, 2 warnings`(基线 156 全过 + 新增 2 项;其中基线
  `test_load_skill_codegen_review_fixer_references` 因运行期间旧断言读到已重写的
  errors.md 先行失败,属预期;更新断言后全绿)。
- 新测试单独跑:`2 passed`(errors_md 契约 + experience_hits 进 LLM context)。

## 变更文件

| 文件 | 改动 |
|---|---|
| `agents/kingdee_plugin_agent/skills/compile-fixer/references/errors.md` | 重写为纯方法论,删全部静态映射 |
| `agents/kingdee_plugin_agent/skills/compile-fixer/SKILL.md` | 措辞同步(交付描述/根因定位/级联示例/参考文件) |
| `agents/kingdee_plugin_agent/skills/loader.py` | compile-fixer 摘要尾句 |
| `agents/kingdee_plugin_agent/prompts/w5_compile.md` | load_skill 交付描述 |
| `tests/test_kingdee_agent.py` | 更新 1 项断言 + 新增 2 项测试 |
| `agents/kingdee_plugin_agent/CLAUDE.md` | skills/ 行补 errors.md 纯方法论说明 |
| `CHANGELOG.md` | 追加 v1.6.1 |
| `docs/superpowers/plans/errors-md-split-report.md` | 本报告 |

未改动:`w5_compile.py`(检索路径已动态-only)、`seed/compile_errors.json`、`seed/seed_load.py`、`common/rag.py`。

## 自审

- 契约强度:errors.md 断言为零 `CS\d{4}` + 零"经验条目(seed)",后续往 skill 里
  塞静态映射会被测试拦住;seed 里加新条目不触犯该契约(种子走经验库)。
- 措辞一致性:SKILL.md / loader 摘要 / w5_compile.md 对"具体映射在哪"的说法统一
  为经验库(启动种子 + w7 沉淀)。
- 既有测试兼容:test_skill_summary("5 轮")、test_load_skill_all_five_skills
  ("方法论" in content)、test_compile_error_retrieves_experience(search_related
  调用次数与附注格式)均不受影响。

## 关注点

1. **errors.md 不再含任何错误码示例**——LLM 未命中经验库时只能靠分析维度自洽
   修复;种子 5 条之外的错误(如新 CS 码)首次出现时经验库必然未命中,靠方法论兜底,
   该行为与重构前一致(旧 errors.md 的静态表也只覆盖 5 条),无回归。
2. **经验库命中的格式契约**(`[错误码] 错误信息 修复:修法`)目前散在 SKILL.md 输入
   段与 w5_compile.md,未收进 errors.md;若未来做格式校验(如解析 experience 附注),
   建议把该格式定义收进 errors.md 检索策略一节(本次未动,避免范围蔓延)。
3. 执行期间并发跑了一次被污染的全量基线(155+1,失败项即被更新的旧断言),
   最终以改动完成后全量 158 绿为准。
