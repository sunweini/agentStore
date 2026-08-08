# 方法论 skills 化实施报告(methodology-skills-report)

日期:2026-08-08 · 分支:main · 提交:`feat(skills): 每 worker 方法论 skill(design/codegen/review/compile-fix)+ prompt 变薄`

## 背景与目标

kingdee-plugin-agent 的 w2-w5 四阶段方法论此前全部内联在 worker prompts 里,
与 `load_skill` 工具形成两套维护面。本任务把方法论收敛进 skills(单源),
prompts 只留 角色 + 输入输出契约 + load_skill 提示。

## 创建的 skills(5 个,含既有 requirement-clarify)

| skill | 阶段 | 文件 |
|---|---|---|
| requirement-clarify | w1(既有) | SKILL.md + bill/service/list.md(老形态:模板直放 skill 目录) |
| **design-builder** | w2 设计 | SKILL.md + references/{bill,service,list}.md(三套完整检查清单:触发操作/控件映射/拦截方式/联动单据/异常骨架/验收自检,每项含"检查自问") |
| **code-generator** | w3 生成 | SKILL.md + references/{bill,service,list}.md(模板优先/指南参数化/冲突以模板为准/占位符清零 + 自检清单) |
| **code-reviewer** | w4 审查 | SKILL.md + references/{bill,service,list}.md(规范库整库对照/API 抽查/模板基线比对/Critical-Important-Minor 裁决规则) |
| **compile-fixer** | w5 编译修复 | SKILL.md + references/errors.md(错误分类表 A-E 五模式:缺引用/签名不匹配/拼写作用域/语法残留,基于 seed/compile_errors.json 5 条扩展为方法论级:CS0246/CS0103/CS0234/CS1061/CS0506 根因链+修法+修复纪律) |

每个 SKILL.md 结构:目标 / 输入 / 流程步骤 / 输出契约 / 踩坑与纪律,
内容为可执行级(LLM 按它干活),非概述。

## Prompt 变薄方案(选择 + 理由)

**选择:最小侵入方案(Option A)** —— 保留 TYPE_PROMPTS 机制,值改为
`skills/<skill>/references/<type>.md` 形态("design-builder/references/bill.md"),
base._load_prompt 对含 "/" 的名字按 skills 根解析;9 个类型分支文件
(w2/w3/w4 × bill/service/list,任务描述说 12 个,实际 9 个 —— w5 无类型分支)
删除,内容并入对应 skill references。

未选"worker 不读类型文件、类型方法论全经 load_skill 供给"(Option B)的理由:

1. **测试契约**:`test_design_type_prompt_mapping` 断言 `TYPE_PROMPTS["bill"]`
   以 "bill.md" 结尾且 keys = {bill, service, list} —— Option A 值
   "design-builder/references/bill.md" 满足;Option B 需改该测试。
2. **零行为变化**:llm=None 确定性骨架路径(w2 骨架设计文档/w3/w4 prompt)
   内容与改前等价,worker 测试零改动全绿。
3. **生产安全**:类型检查清单始终在上下文内 —— LLM 工具调用可能不稳定
   (漏调 load_skill 就完全没有类型方法论,质量劣化不可见);
   Option A 是"prompt 内联 + load_skill 全量"双通道,同源文件,无重复维护。
4. 磁盘上类型方法论只有一份(skills 文件),load_skill 与 worker 读同一文件,
   达成单源目标;运行期双通道交付是特性不是缺陷。

## Loader 变更

- `_AVAILABLE_SKILLS` 4 → 5 项(渐进式披露摘要层,含设计/生成/审查/编译修复摘要)
- `load_skill` references glob 兼容两种形态:skill 目录根 *.md(requirement-clarify)
  + references/ 子目录 *.md(4 个新 skill),name→content 映射交付不变
- `SKILL_HINT` 更新:5 个 skill 按阶段列出(需求澄清/设计/生成/审查/编译修复)

## 测试结果

- `pytest tests/ -q` → **156 passed**(152 既有 + 4 新增)
- 新增 4 项:
  - test_load_skill_all_five_skills:5 skill 全可加载(content 含方法论 + references 非空)
  - test_load_skill_design_builder_references:design-builder 三类型 references 关键内容断言(事件绑定决策/触发操作/拦截方式/事务边界/操作按钮/逐行)
  - test_load_skill_codegen_review_fixer_references:code-generator(模板优先/三类基类)/code-reviewer(Critical/Needs fixes/AfterDoOperation/回滚补偿)/compile-fixer(5 轮/CS0246/CS1061/CS0506)
  - test_worker_type_branches_read_from_skill_references:TYPE_PROMPTS 指向 skills references(单源化不回归)
- test_skill_summary 更新:`set(summary) == 5 skill` + 每 skill 关键摘要词

## 踩坑记录(重踩 dev-standards §7.2)

新 w4_review.md 写 JSON 契约样例时用了**单花括号** `{"severity": ...}`,
经 ChatPromptTemplate f-string 解析在 format 阶段抛错 → w4 的 `_llm_review`
异常兜底 → 静默回退确定性骨架 → 脚本化 Critical findings 丢失 →
test_graph_rework_loop_review_needs_fixes 裁决变 Approved、返工预算不扣。
修复:恢复原 prompt 的 `{{...}}` 双花括号转义。教训:prompts 与 skill references
被拼进系统提示后同样经 f-string 解析,含 `{}` 样例必须转义。

## 变更文件

- 新增:skills/{design-builder,code-generator,code-reviewer,compile-fixer}/{SKILL.md, references/*.md}(4 SKILL + 10 references)
- 修改:skills/loader.py(_AVAILABLE_SKILLS/SKILL_HINT/references glob)、
  prompts/{w2_design,w3_generate,w4_review,w5_compile}.md(变薄)、
  graph/workers/base.py(_load_prompt "/" 路径)、
  graph/workers/{w2_design,w3_generate,w4_review}.py(TYPE_PROMPTS 指向 skill + docstring)、
  tests/test_kingdee_agent.py(4 新增 + 1 更新)、
  agents/kingdee_plugin_agent/CLAUDE.md(架构表 + 常用操作 skill 目录说明)、
  CHANGELOG.md(v1.6.0)
- 删除:prompts/ 下 9 个类型分支文件(w2_design_* / w3_generate_* / w4_review_*)

## 自查

- [x] 5 skill 全可加载,references name→content 交付
- [x] 类型分支方法论 prompts 与 skills 同源(worker TYPE_PROMPTS 指向 skill 文件)
- [x] 4 个 base prompt 去方法论,保留角色 + 契约 + load_skill('<skill>') 提示
- [x] 既有 worker 测试零改动全绿(仅 test_skill_summary 断言集更新)
- [x] 全量 156 项通过

## 关注点

1. **Option A 的运行期双通道**:worker 拼入的类型要点与 load_skill 交付同源同文,
   无维护双份问题;但 token 上有少量重复(类型要点在 prompt + 工具结果各一份)。
   若后续要省 token,可切 Option B(worker 不拼分支,类型名进 prompt),届时需改
   test_design_type_prompt_mapping 与确定性骨架内容,属行为变更,建议单独做。
2. **references/errors.md 是静态方法论文档**:与 seed/compile_errors.json(经验库
   种子)并存 —— 前者是给 LLM 的模式库,后者是经验库数据,有意不合并;
   未来经验库条目增长时,errors.md 需人工同步高频新模式。
3. **compile-fixer references 只做了 errors.md**:任务要求即此(w5 无类型分支),
   bill/service/list 类型要点未进 compile-fixer(编译修复与类型无关,错误模式
   已覆盖三类型签名)。
4. **load_skill 线上绑定仍未验证**(沿用 v1.5.0 既有债务):真实 DeepSeek 首次
   联调时先跑 w1 smoke,若 tools+json_schema 被拒改 sentiment JSON Mode 模式。
