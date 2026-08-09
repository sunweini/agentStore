# SKILL 评估改进报告(skill-creator 评估反馈迭代)

日期:2026-08-10 分支:main(head 182bfc4)→ commit `docs(skills): 评估改进 — code-generator 禁编造反例 + knowledge-steward verify 必填`

## 背景

skill-creator 评估发现两条纪律短板(without-skill 对照,LLM 未调 load_skill):

1. **code-generator 禁编造纪律不够强**:无 skill 时 LLM 编造 `InvServiceHelper.QueryInvQty` /
   `InvQueryParam` / `InvQueryResult.AvailableQty` 等看似真实的 API(注释伪装成
   真实接口),编译必挂。
2. **knowledge-steward 无两态纪律**:无 skill 蒸馏无 proposed/verified 两态、
   无验证路径概念。

## 改动清单

### Finding 1 — code-generator(禁编造 API 纪律强化)

`agents/kingdee_plugin_agent/skills/code-generator/SKILL.md`:

- 新增「**禁止编造 API(签名必须有来源)**」独立章节(位于踩坑与纪律之后):
  - **坏例**:编造的 API 调用(InvServiceHelper.QueryInvQty 等,带看似真实的
    参数/返回类型注释)标注"编译必挂,注释还让它显得像真的";
  - **好例**:显式 TODO 骨架 + return 默认值 + 注释"签名未在元数据/guide
    确认,禁止编造;TODO(接线)检索到真实签名后补全";
  - **来源标准**:库存查询/服务调用/WebAPI 接口的签名必须有来源(guide 检索
    命中/元数据确认/模板既有调用),三者皆无一律 TODO 占位,禁止凭记忆补全;
    判定标准一句话:"签名能说出处才写,说不出处 = 没来源 = TODO";
  - **为什么**:编造 → 编译失败 → 烧掉整条编译-修复循环(w5 ≤5 轮 + 返工
    预算 + w4 打回);TODO 占位零成本编译通过,由后续元数据接线补全;带注释
    的假 API 与真代码无法区分,唯一防线是"无来源不写"纪律本身。
- 流程 step 2「指南参数化」与 step 5「验收自检」、踩坑纪律"假字段/假方法名"
  条目同步指向新章节。

`agents/kingdee_plugin_agent/skills/code-generator/references/bill.md`(该文件经
ChatPromptTemplate 渲染,样例未含花括号,规避 f-string 冲突):

- 新增"服务调用不编造(库存查询/跨单据读取等外部 API)"要点 + 自检清单项
  ("服务调用/库存查询等外部 API 均有来源,无来源的已 TODO 占位,未凭记忆补写")。

### Finding 2 — knowledge-steward(proposed 必带 verify 建议)

`agents/kingdee_plugin_agent/skills/knowledge-steward/SKILL.md`:

- 流程 step 1「不沉淀」判据补"想不出验证路径的观察不沉淀"(无法验证 = 永远
  proposed = 检索噪音);
- 流程 step 2「条目格式」:proposed 态追加 `验证:` 字段且**必填**(复现方式
  或人工确认人;种子为 verified 基线无需携带);
- 流程 step 4:propose 时填的验证字段 = 后续 review 的作业清单(照单复现或
  找确认人;对不上 = 还不到 verify 时候);
- 踩坑纪律新增"**proposed 无验证路径 = 污染源**":无验证路径的条目永远无法
  翻转 verified,却以 unverified 进检索被自核后采用 —— 幻觉修复被当知识,
  验证建议是防污染的收口。

`agents/kingdee_plugin_agent/skills/knowledge-steward/references/distillation.md`:

- 条目模板新增「验证字段(proposed 态必填)」小节(复现方式/人工确认人/种子
  免携带/"想不出验证路径 → 这条不沉淀");
- 好例新增带验证字段的 proposed 形态示例;坏例新增"验证:无"示例(无验证路径
  不沉淀);
- 「proposed → verified 判据」补验证字段作为 review 作业清单的说明。

### 摘要层(loader.py)

`agents/kingdee_plugin_agent/skills/loader.py` `_AVAILABLE_SKILLS` 摘要同步
强化(评估是 without-skill 场景,摘要层是 LLM 直接看到的部分):

- code-generator 摘要:"签名必须真实" → "签名必须真实(无来源外部 API 一律
  TODO 占位,禁止编造)";
- knowledge-steward 摘要:补 "proposed 必带验证建议"。

## 测试

- 契约测试(load_skill 内容断言):`pytest tests/test_kingdee_agent.py -k
  "load_skill or skill_summary or worker_type_branches or errors_md"` → **8 passed**。
  所有被断言的短语(模板优先/占位符/AbstractBillPlugIn/proposed/verified/
  code|file_pattern/不阻塞/无 LLM/api_ref/bm25_weight/0.7/L2/RRF/条目模板/
  好例/坏例/signature/去重/proposed → verified)原样保留,未改任何测试。
- 全量:`pytest tests/ -q` → **272 passed**(基线 272,持平)。

## 变更文件

- `agents/kingdee_plugin_agent/skills/code-generator/SKILL.md`
- `agents/kingdee_plugin_agent/skills/code-generator/references/bill.md`
- `agents/kingdee_plugin_agent/skills/knowledge-steward/SKILL.md`
- `agents/kingdee_plugin_agent/skills/knowledge-steward/references/distillation.md`
- `agents/kingdee_plugin_agent/skills/loader.py`
- `CHANGELOG.md`(v1.20.0)

## 关注点

- **无代码行为变更**:知识层(方法论文本)+ 摘要措辞改动,ExperienceStore/
  worker 逻辑未动。验证字段是文本模板层面的强制(review 与未来 LLM 化参照),
  ExperienceStore.propose 暂不携带 verify 元数据列 —— 若后续要在检索侧透传
  验证建议,需在 store 元数据加字段(本次未做,属可选增强)。
- **w7 运行时产物仍为"w7 沉淀,待人工验证"占位 fix**:现有已知取舍,本文
  方法论将 review 时补全修法 + 验证字段作为标准,不改变 w7 确定性代码。
- **references 花括号约束**:code-generator/references 会经 ChatPromptTemplate
  渲染,新增内容刻意未含字面花括号;SKILL.md 仅经 load_skill JSON 交付,样例
  用真实 C# 花括号安全。
