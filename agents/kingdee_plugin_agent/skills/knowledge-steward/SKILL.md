# 金蝶插件知识库全生命周期方法论(knowledge-steward)

配合 w7 知识沉淀阶段与知识库日常维护使用。系统提示已注入本 skill 摘要;
需要完整方法论时调用 `load_skill("knowledge-steward")` 获取本文与
references/(distillation.md 沉淀质量标准 + maintenance.md 维护操作手册)。

> **当前 w7 为确定性代码(无 LLM 调用),不绑定 load_skill**:沉淀决策(哪些
> compile_errors 值得沉淀)由代码规则完成 —— 编译错误全量进 proposed 态。
> 本文"沉淀方法论"是人工 review 与未来 LLM 化时的参照标准,不参与 w7 运行时。

## 目标

让知识库(api_ref / guide / experience 三向量库 + standards 整库注入规范库)
长期保持**高质量、低冗余、可检索**:

- 沉淀侧(w7):踩坑只沉淀可复现的错误模式,proposed 先行、人工验证后 verify,
  失败不阻塞交付;
- 维护侧(人工):种子增补、文档导入、规范库合并、定期 review,全部幂等
  (文档导入为**去重式幂等** —— 仅对未变更内容成立,编辑已灌入文档须
  `--delete-source` 删旧再重灌,否则新旧版本并存);
- 检索侧(全 worker):路由正确、检索参数语义统一,不让分数方向坑人。

## 输入

- `subtask.compile_errors`:本轮编译错误列表(每条含 code/message)
- 经验库(ExperienceStore):proposed/verified 两态条目 + "code|file_pattern" 签名
- 种子数据:seed/compile_errors.json(启动幂等灌入,10 条基准,含真实环境 MSB3274/3275 等)
- 维护场景输入:官方文档爬取结果 / 内部资料 / 规范新增

## 流程(沉淀方法论,w7 绑定时用)

1. **判断什么值得沉淀**(标准,当前由人工/未来 LLM 执行;w7 代码暂全量 proposed):
   - **沉淀**:可复现的错误模式 —— 有明确 code+message 特征、根因链清晰、
     修复配方可复用(如 CS0246 缺 Kingdee.BOS 引用 → csproj 加 Reference);
   - **不沉淀**:一次性错误(环境抽风、网络抖动、纯上下文偶发)、无法归因的
     错误、想不出验证路径的观察 —— 沉淀了只会污染检索(无法验证 = 永远停在
     proposed = 检索噪音);
   - 编译错误类判据:有明确错误码(CSxxxx)且 message 呈现稳定特征 → 可沉淀;
     错误码 + 根因 = 修复配方的核心索引。
2. **条目格式**:`[code] message 修复:fix`,proposed 态**追加 `验证:` 字段且
   必填** —— 复现方式("真实编译环境复现 CS0246 后按修法修复通过")或人工
   确认人("由 XX 工程师审阅确认")。种子条目为 verified 基线,无需携带;
   完整条目模板与好例/坏例对比见 references/distillation.md。
3. **签名去重**:signature = `code|file_pattern`。同签名已存在 → 不重复入库
   (propose 幂等,直接返回既有签名);同 code 不同 file_pattern 是两条独立条目;
   验收拒绝走同通道且 file_pattern 必须含 sha256 摘要(防全部拒绝共享同一签名)。
4. **proposed → verified**:沉淀一律先 proposed 态(带 source="w7"),**人工或
   复现验证**后才 verify(仅翻转元数据 status,文档与向量不动)。proposed 条目
   检索时标注 confidence="unverified",仅供参考、自核后采用。propose 时填的
   **验证字段 = 后续 review 的作业清单**:翻转前照字段复现,或找字段写明的
   确认人确认;验证字段对不上 → 该条目还不到 verify 时候。
5. **不阻塞纪律**:沉淀失败(经验库不可用/写入异常)不得阻塞交付 —— 上报
   DONE_WITH_CONCERNS 并记待沉淀队列,后续人工补录。

## 检索路由速查表(全 worker)

| 库 | 内容 | 检索方式 | 使用 worker |
|---|---|---|---|
| api_ref | 金蝶 BOS API 参考片段 | hybrid_search(bm25_weight=0.7 约定,精确 API 名优先) | w2 设计(按子任务标题) |
| guide | 团队开发向导/指南 | hybrid_search + filter={plugin_type} 类型过滤 | w2 设计、w3 生成 |
| experience | 编译错误经验(seed + w7 沉淀) | search_related(错误码+消息语义向量检索;w2 用标题语义,title 同时充当 code/message 双信号) | w2 设计(标题检索历史坑,命中注入设计上下文"历史踩坑参考",verified 优先、作规避参考非必须满足)、w5 修复(命中附注 experience,自核后采用;**category="env" 的环境类命中 → 升级 BLOCKED 附运维提示,不进修复轮次、不计轮次不扣预算**) |
| standards | 规范库(markdown 整库注入,不建向量索引) | StandardsLoader.inject_text(8k token 预算) | w4 审查(整库逐条对照) |

- 分数方向警示:`search()`/`search_related()` 返回 Chroma L2 距离,**越小越
  相关**;`hybrid_search()` 返回加权 RRF 融合分,**越大越相关**。两者方向相反、
  量纲不同,**不可跨方法比较**,阈值/排序逻辑各自解释,勿混用。
- bm25_weight 约定:api_ref 检索传 0.7(精确 API 名优先,rag.py 约定);
  guide 用默认 0.5。当前 worker 实现仍走默认值,后续调参按本表约定。
- w4 的"API 抽查"(事件签名/using 引用核对)凭模型知识与模板基线比对完成,
  **未接 api_ref 检索**(ReviewWorker 不检索 api_ref);接入检索属后续增强。

## 维护手册(人工读)

- **种子增补**:改 seed/compile_errors.json 加条目(格式见蒸馏标准),
  seed_load 幂等灌入(按签名查重跳过),跑测试确认断言 n>=7。
- **文档导入**:官方文档爬取/内部资料 → api_ref/guide 分库入库 —— 走 RAG 导入
  管线 `python -m agents.kingdee_plugin_agent.tools.ingest`(`--url <URL>`
  / `--dir <目录>` / `--seed-internal` + `--collection api_ref|guide`;代码感知
  分块;幂等是**去重式** —— 内容未变重跑新增 0,**编辑已灌入文档后必须
  `--delete-source` 删旧再重灌**,否则新旧版本并存),导入后 hybrid_search
  抽查验证命中,分步见 references/maintenance.md §2。
- **规范库合并**:规范以 markdown 整库注入,新增文件放入 standards 目录即可;
  超 8k token 预算自动截断并标注转 guide 检索兜底 —— **只提议人工合并,不自动
  改写规范**;w7 只建议,合并不在 w7 职责内。
- **经验库定期 review**:节奏建议每周;review proposed → 复现/人工确认后 verify;
  归档标准见 references/distillation.md(条目模板 + 去重边界)。

## 输出契约

- w7(运行时):compile_errors 逐条 propose 成功 → DONE(evidence 沉淀完成);
  失败 → DONE_WITH_CONCERNS(evidence 含错误信息 + 记待沉淀队列)。
- 人工维护:种子增补/规范合并/review 均幂等可重跑;文档导入为**去重式幂等**
  —— 内容未变重跑新增 0,编辑已灌入文档后须 `--delete-source` 删旧重灌,
  否则新旧版本并存。

## 踩坑与纪律

- **proposed 不是终点**:只 propose 不 verify,经验库会积累大量 unverified 噪音;
  定期 review 把验证过的条目翻转 verified。
- **proposed 无验证路径 = 污染源**:没有复现方式或确认人的条目永远无法翻转
  verified,却仍以 unverified 姿态进检索、被自核后采用 —— 幻觉修复/未验证
  观察被当知识用,是经验库最大的污染风险。propose 时就写清"怎么证明它是对的"
  (验证字段必填),验证建议就是防污染的收口。
- **fix 占位不是修法**:w7 沉淀的 fix 是占位文案("w7 沉淀,待人工验证"),
  verify 时补全真实修法,否则条目只有"症状"没有"配方"。
- **去重吞并风险**:恒空 file_pattern 会让同 code 所有条目共享同一签名
  (如 "CS0246|"),后续不同场景的坑会被去重吞掉 —— 需要区分场景时用
  file_pattern 或 sha256 摘要做签名位。
- **分数方向混用 = 排序错乱**:同一处代码不要拿 search 的分数当 hybrid 的
  分数比较;各检索方法的分数语义见上表,取最大/最小要按方法区分。
- **规范库超限 ≠ 规范缺失**:超预算截断只影响注入文本,规范文件本身完整,
  兜底走 guide_fallback(guide 库检索),不要把"注入被截断"误报为"规范丢失"。

## 参考文件

- `references/distillation.md` — 沉淀质量标准(条目模板/好例坏例/去重边界/签名规则)
- `references/maintenance.md` — 维护操作手册(种子增补/文档导入/规范合并/review 分步)
