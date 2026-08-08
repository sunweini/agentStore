# knowledge-steward skill 交付报告

日期:2026-08-08 · 分支:main · 提交:4fae8ce

## 交付内容

1. **skill 目录** `agents/kingdee_plugin_agent/skills/knowledge-steward/`(新建 3 文件):
   - `SKILL.md` — 知识库全生命周期方法论(目标/输入/流程/输出契约/踩坑纪律骨架):
     - **沉淀方法论**(w7 绑定时用):什么值得沉淀(可复现错误模式/根因链/修复配方,一次性错误不沉淀;编译错误类以 code+message 稳定特征为判据)、条目格式 `[code] message 修复:fix`、signature = `code|file_pattern` 去重语义、proposed→verified 流程(先 proposed,人工/复现验证后 verify)、不阻塞纪律(失败 → DONE_WITH_CONCERNS + 待沉淀队列);
     - **维护手册**(人工读):种子增补(格式/幂等语义)、文档导入(官方文档 → api_ref/guide 分库,分块+元数据+检索抽查)、规范库合并(整库注入,8k token 预算超限截断标注 → guide_fallback 检索兜底,只提议人工合并)、经验库定期 review(每周或 ~10 条节奏,verify/归档标准);
     - **检索路由速查表**:api_ref/guide/experience 三库 × w2-w5 检索方式(表中注明:实际代码 w2 = api_ref + guide 类型过滤;w3 = guide 类型过滤;w4 = standards 整库注入 + API 抽查;w5 = experience.search_related 语义检索;w7 = experience 写入);hybrid_search bm25_weight 约定(api_ref 0.7,rag.py 既有约定;当前 worker 走默认 0.5,后续调参按表);**分数方向警示**:search L2 距离低=好 vs hybrid RRF 融合分高=好,不可跨方法比较。
   - `references/distillation.md` — 沉淀质量标准:条目模板(好例/坏例对比:坏例含 fix 占位、message 带路径行号、一次性错误)、去重边界(同 code 不同 file_pattern 两条;恒空 file_pattern 签名吞并风险;验收拒绝 sha256 摘要入签名)、签名规则速记表、proposed→verified 判据(复现验证/人工确认二选一)。
   - `references/maintenance.md` — 维护操作手册四步走(种子增补/文档导入/规范库合并/定期 review),每步含何时做、操作步骤、验证方式、注意点;全部幂等可重跑。

2. **loader.py 注册**:`_AVAILABLE_SKILLS["knowledge-steward"]` 摘要(沉淀方法论 + 维护手册 + 检索路由);`SKILL_HINT` 按阶段提示追加 `知识沉淀(knowledge-steward)`(结构检查:SKILL_HINT 是按阶段列 skill 的提示,w7 阶段对应 knowledge-steward,已补上;w1-w5 LLM 持有 load_skill 工具,按需可取路由表)。

## w7 绑定决策:不绑定(无 LLM 调用)

**结论:不加 load_skill 绑定。** 依据:w7_distill.py 为纯确定性代码 —— `_execute` 直接遍历 `subtask.compile_errors` 逐条 `experience.propose(code, "", message, "w7 沉淀,待人工验证")`,构造函数虽接收 llm 参数但从未使用(对比 w1-w5 均经 `structured_with_skill` 出结构化输出)。沉淀决策("哪些 compile_errors 值得沉淀")当前由代码规则完成(全量 propose,不判断);给无 LLM 的节点挂 load_skill 工具等于空转,徒增维护面。

处理方式(按任务要求):SKILL.md 顶部加显式说明 —— "当前 w7 为确定性代码(无 LLM 调用),不绑定 load_skill;沉淀决策由代码规则完成,本文'什么值得沉淀'标准是人工 review 与未来 LLM 化的参照"。即:w7 运行时不消费本 skill,skill 是知识层文档(人工维护 + 未来把 w7 改成 LLM 决策时直接可绑定)。已有测试断言该说明存在("无 LLM" 在 SKILL.md content)。

## 测试

- 新增 1 项 `test_load_skill_knowledge_steward`(SKILL.md 含 api_ref/bm25_weight 0.7/L2 vs RRF/proposed→verified/无 LLM 说明;references = {distillation.md, maintenance.md} 内容断言);`test_skill_summary` 更新为 6 项断言;`test_load_skill_all_five_skills` 更名 `all_six_skills` 并纳入第 6 个。
- 全量:`.venv/bin/python -m pytest tests/ -q` → **159 passed**(158 基线 + 1 新增),65s,绿。

## 变更文件

- 新增:`agents/kingdee_plugin_agent/skills/knowledge-steward/SKILL.md`、`references/distillation.md`、`references/maintenance.md`
- 修改:`agents/kingdee_plugin_agent/skills/loader.py`(注册 + SKILL_HINT)、`tests/test_kingdee_agent.py`(1 新增 + 2 更新)、`agents/kingdee_plugin_agent/CLAUDE.md`(skills/ 行 5→6 个 + knowledge-steward 说明)、`CHANGELOG.md`(v1.7.0)

## 自审

- 检索路由表按**实际代码**写(w3 仅 guide、w2 api_ref+guide),与任务描述的"w3 生成:同"有出入 —— 以代码为准并在表中如实标注,w2/w3 列已分开写。
- standards 目录无固定路径(StandardsLoader 由 build_graph 注入),maintenance.md 已改为"注入目录"表述,不写死仓库路径。
- skill 内容仅经 load_skill JSON 交付(不拼进任何 worker 的 ChatPromptTemplate),无需 `{}` 转义;SKILL_HINT 新增段无花括号,安全。
- 与既有契约一致:签名格式/种子格式/w7 propose 形态(占位 fix)均如实记录,不美化现状(w7 沉淀的 fix 占位被标为"verify 时必须补全"的已知取舍)。

## 关注点

- **w7 fix 占位质量问题**是现存设计取舍(非本次引入):w7 propose 的 fix 是"w7 沉淀,待人工验证",条目只有症状没有配方;distillation.md 已写明 verify 时补全。若未来要让沉淀闭环,建议把 w7 改为 LLM 决策(绑定本 skill,按"什么值得沉淀"标准过滤 + 生成真实修法),本 skill 即为该改动的运行时输入。
- **bm25_weight 0.7 约定尚未落到 worker 代码**(当前全部默认 0.5);本 skill 记录了约定,调参属独立小改动,未顺手改(避免无测试覆盖的静默行为变化)。
- SKILL_HINT 现在向 w1-w5 提示了 knowledge-steward(含路由表),w2/w4/w5 的 LLM 理论上可调它取检索指引 —— 属预期收益;w7 无 LLM 不消费。

---

# 评审修复补充(2026-08-08,提交见下)

评审结论:1 Important + 2 Minor,全部修复,全套测试 162 过(159 + 3 新增)。

## 修复清单

1. **Important — seed_load 灌入命令 no-op**:`seed_load.py` 原无 `__main__` 块,文档命令 import 后退出什么都没灌。修复:新增 `main(argv)` + argparse(`--data-dir` 可选,默认 data/kingdee-rag 与 RagClient 一致),打印 "种子灌入完成:新增 N 条";`maintenance.md` 步骤 1.4 同步真实调用。冒烟验证(真实子进程):首跑 "新增 7 条",二次 "新增 0 条"(幂等生效)。测试选**进程内直接调 main()**(capsys 断言打印契约)—— 与子进程相比不额外加载嵌入模型,且覆盖同一 argparse 路径,更干净。
2. **Minor 2 — 路由表高估 w4 api_ref 使用**:ReviewWorker 不检索 api_ref(rag 注入但 _execute 只用 standards.inject_text)。修复:SKILL.md 路由表 api_ref 行删掉 w4,加脚注 "w4 的 API 抽查(事件签名/using 引用核对)凭模型知识与模板基线比对完成,未接 api_ref 检索;接入检索属后续增强"。
3. **Minor 3 — 归档步骤无 API**:修复:`ExperienceStore.archive(signature)` 新增(与 verify 共用 `_set_status` 重构路径,status → archived,文档与向量不动);`search_related` 既有过滤(仅 proposed/verified 返回)天然排除 archived,已验证;`maintenance.md` 步骤 4 改用该 API。

## 测试

- tests/test_rag.py 新增 2 项:archive 流程(proposed/verified 均可归档 → search_related 排除 → 文档与元数据仍在库内,status=archived)/ archive 未知签名抛 RagError;
- tests/test_kingdee_agent.py 新增 1 项:seed_load CLI main 冒烟(首跑 n>=7 + 打印契约 + 二次幂等 0);
- 全量 `.venv/bin/python -m pytest tests/ -q` → **162 passed**(159 基线 + 3 新增),105s,绿。

## 变更文件

- 修改:`agents/kingdee_plugin_agent/seed/seed_load.py`(__main__ + main())、`common/rag.py`(ExperienceStore._set_status 重构 + archive())、`agents/kingdee_plugin_agent/skills/knowledge-steward/SKILL.md`(路由表脚注)、`skills/knowledge-steward/references/maintenance.md`(步骤 1.4/4)、`tests/test_rag.py`、`tests/test_kingdee_agent.py`、`CHANGELOG.md`(v1.7.1)

## 自审

- archive 的过滤侧是既有逻辑(非新加):search_related 对 status 不在 proposed/verified/None 的分支直接 continue —— 新增测试覆盖确认。
- 种子灌入 CLI 的 `--data-dir` 默认值与 RagClient 默认值一致(data/kingdee-rag),避免文档与实际不一致。
- SKILL.md 测试断言("api_ref" in content)在脚注修改后仍成立,无需改测试。

## 关注点

- 无新增:修复后唯一遗留建议仍是 w7 fix 占位质量(前报关注点,非本次范围)。
