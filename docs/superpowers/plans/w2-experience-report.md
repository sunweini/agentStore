# w2 设计阶段经验库回流 — 交付报告

日期:2026-08-08
提交:`feat(w2): 设计阶段经验库回流(历史坑 → 设计规避)+ 路由表/方法论更新`
范围:w2 DesignWorker 接 ExperienceStore 检索,历史踩坑在设计阶段注入设计上下文,减少 review 拒绝与编译修复轮次。

## 实现内容

### 1. w2_design.py(核心)

- `DesignWorker.__init__` 增加 `experience=None` 参数(与既有 `rag=None` 同模式,注入式可选依赖)。
- 新增 `_retrieve_experience(subtask)`:按子任务标题调 `experience.search_related(subtask.title, subtask.title, k=3)`,命中返回前做 **verified 优先稳定排序**(`sorted(hits, key=lambda h: h["metadata"].get("confidence") != "verified")`,proposed 经设计判断后采用);整体 try/except → 故障降级返回空,不阻塞设计(与 `_retrieve` RAG 检索同一纪律)。
- `_llm_design` 检索后把命中并入 context JSON:`experience: [{text, status, confidence}]`(种子条目无 status 视作 verified);有命中时 human 消息追加"历史踩坑参考"段 —— 显式标注"仅供参考、非必须满足",并给出示例(签名级联 → 设计时核对基类事件签名)。
- 注入安全:命中文本只经 `{context}` 占位符(JSON dump)进模板,追加的静态提示无花括号,不踩 ChatPromptTemplate f-string 转义坑(dev-standards §7.2)。
- 确定性骨架路径(llm=None/LLM 失败)不注入经验 —— 降级路径保持骨架原样,经验注入只发生在真实 LLM 上下文。

### 2. agent.py

- `"w2": DesignWorker(..., experience=experience)` 透传(原仅 w5/w7 收到 experience);`build_graph` docstring 同步为 "w2 设计历史坑参考 / w5 修复检索 / w7 沉淀"。

### 3. knowledge-steward/SKILL.md 路由表

- experience 行:使用 worker 从 "w5 修复" 扩为 "w2 设计(标题检索历史坑,命中注入设计上下文'历史踩坑参考',verified 优先、作规避参考非必须满足)、w5 修复(命中附注 experience,自核后采用)";检索方式注明 "w2 用标题语义,title 同时充当 code/message 双信号"。

### 4. design-builder/SKILL.md 方法论

- 流程步骤插入第 3 步"查历史踩坑(经验库)":设计前先查历史坑,把已知错误模式转化为设计规避(如签名级联 → 设计时核对基类事件签名,对照 templates/<type>/template.cs 基准);命中只作规避参考、不改变输出骨架、不引入新需求。后续步骤 4-8 顺延为 5-9。
- 输入清单补经验库检索上下文条目。

### 5. 测试(tests/test_kingdee_agent.py 新增 2 项)

- `test_w2_experience_hits_reach_design_context`:fake experience + 捕获消息的 fake LLM —— 断言调用契约为 `search_related("审核校验", "审核校验", 3)`(标题双信号 + k=3)、human 消息含"历史踩坑参考"与两条命中文本、verified 条目排在 proposed 前、设计文档正常落盘。
- `test_w2_experience_failure_degrades_to_done`:经验库抛 RuntimeError → 仍 DONE、design.md 落盘(降级纪律)。
- 既有 w2 测试(experience 缺省 None)未改动。

### 6. 文档

- `agents/kingdee_plugin_agent/CLAUDE.md` 常用操作新增"接经验库"条目(w2 检索语义与降级纪律)。
- `CHANGELOG.md` 追加 v1.8.0。

## 调用签名核实

`common/rag.py::ExperienceStore.search_related(error_code, message, k=3)`(L300-318):

- 签名与任务描述一致:两位置参数 + k 关键字参数;
- 内部执行 `client.search("experience", f"{error_code} {message}", k=k)` —— 两个入参拼接进查询串,故 w2 传 `(title, title)` 等价于单语义查询、并预留了 code/message 两通道的接口形状(未来设计阶段若有代码级信号可换入);
- 返回 `[{text, score, metadata}]`,已过滤为 proposed/verified(种子无 status 视作 verified),proposed 的 metadata 带 `confidence="unverified"`、verified/种子带 `confidence="verified"` —— w2 的 verified 优先排序直接消费该字段,status 字段用于条目标注。

## 测试结果

- 定向:`pytest tests/test_kingdee_agent.py -q -k "design or w2"` → 6 passed。
- 全量:`pytest tests/ -q` → **164 passed**(162 基线 + 2 新增),2 条既有 Starlette deprecation 警告,无新增失败。

## 变更文件

- `agents/kingdee_plugin_agent/graph/workers/w2_design.py`(+43/-3)
- `agents/kingdee_plugin_agent/agent.py`(透传 experience + docstring)
- `agents/kingdee_plugin_agent/skills/knowledge-steward/SKILL.md`(路由表 experience 行)
- `agents/kingdee_plugin_agent/skills/design-builder/SKILL.md`(流程步骤 + 输入)
- `agents/kingdee_plugin_agent/CLAUDE.md`(常用操作)
- `tests/test_kingdee_agent.py`(+62)
- `CHANGELOG.md`(v1.8.0)

## 自审

- 与 w5 同一纪律:经验库故障一律降级不阻断,`_retrieve_experience` 与 `_retrieve` 结构对称;`experience=None` 时零开销。
- 注入内容显式标注"参考非必须",防经验条目被 LLM 当新增需求写进设计(与 w5 的"自核后采用"语义一致)。
- 文本注入路径均经占位符或静态字符串,无模板转义风险。
- 路由表行与实现一致(标题语义、verified 优先、注入设计上下文均已落地),方法论步骤与注入提示一致。

## 关注点

- **标题检索命中率未验证**:设计阶段的 title 是业务描述,与经验库的错误码条目词汇距离可能较大;命中不足时是设计时"查无可查"(返回空,流程无感),不影响正确性,靠后续真实运行观察是否需要调 k 或换检索库。
- **verified 优先是相对排序非过滤**:proposed 条目仍会注入(标 confidence/status),由 LLM 自核 —— 与知识库"proposed 不沉淀决策"的纪律靠提示词约束,LLM 不遵循时无代码强制。
- **确定性骨架路径不注入经验**:llm=None 的测试/降级路径看不到历史坑;可接受(骨架非真实设计),如需可后续扩展。
- 未改 loader 的 knowledge-steward skill 摘要(摘要只提路由表,无需变更)。

## 评审修复(2026-08-08,提交 `test(w2): 经验故障降级测试走真实 LLM 路径`)

### Important — 降级测试原为空转(vacuous),已修复

- **问题**:`test_w2_experience_failure_degrades_to_done` 原用 `DesignWorker(llm=None, experience=BrokenExperience())` —— `_llm_design` 在 `if self.llm is None` 守卫处提前返回,`search_related` 从未被调用(评审实证 0 次调用),核心降级逻辑(检索异常 → 空命中 → 不阻塞设计)未被测试覆盖。
- **修复**:复用捕获消息的 fake LLM 模式(与 hits 测试相同的 `_DesignLLM`,带 `seen` 列表)+ `BrokenExperience()`,断言:
  - `llm.seen` 非空 —— 检索异常被降级为空命中、LLM 仍被调用,证明未阻塞设计;
  - human 消息**不含**"历史踩坑参考"标记 —— 证明降级为空命中而非有命中;
  - design.md 内容为 LLM 产出("# 设计文档(LLM 正常产出)")—— 证明走 LLM 路径而非确定性骨架;
  - `sub.design_path` 正常设置、STATUS DONE。
- **Minor**:hits 测试函数内局部 `DesignOutput` import 提升到模块级(与 DesignWorker/TYPE_PROMPTS 同处一行)。
- 回归:`tests/test_kingdee_agent.py -v` 86 项全过;全量 `tests/ -q` **164 passed**(162 基线 + 2 新增,测试计数不变,降级测试为原 2 项内强化)。
