# kingdee-plugin-agent 文档全面审计同步波 — 交付报告

日期:2026-08-09
范围:仅文档同步(0 代码改动),逐项对照代码验证后落笔。

## 逐文件变更

### 1. README.md(原完全未提 kingdee)

- 项目状态:新增 kingdee-plugin-agent 段落(1 主管 + 8 worker 循环图、交付包内容、w6_fail 失败收尾、212 项测试全过)。
- 目录结构:agents/ 下新增 `kingdee_plugin_agent/` 分支(agent/cli/api/graph/skills/tools/templates/store/seed);新增 `compile_service/`、`web/kingdee-demo.html`、`docs/kingdee-plugin-agent/` 三处;tests 注释 12 → 212。
- 快速开始:新增 kingdee 入口命令(compile-service `docker-compose up -d`、API `uvicorn ...:create_app --factory`、CLI `python -m ...cli "<需求>" --env test`、演示页 kingdee-demo.html)+ curl 建任务示例(`POST /tasks` payload `{"requirement", "env"}` —— 已对照 api.py 核实:`env` 而非 `env_name`,缺省 "test")。
- 测试:12 → 212(已运行 `pytest tests/ --collect-only` 确认 212 项:kingdee 5 文件 194 + sentiment 14 + eval 4)。
- 文档:新增 4 条 kingdee 链接(设计文档 + project/tech/manual 三件套);CHANGELOG 标注 v1.0.0 → 当前 v1.12.0;api.md/deployment.md 条目标注为 sentiment 文档。

### 2. docs/kingdee-plugin-agent/tech.md

- §9 测试规模:164 项(CHANGELOG v1.8.0)→ **212 项(v1.12.0)**,子项描述补 metrics/otel/失败收尾包与 eval 集。
- §2.2 TaskState 表:新增 `metrics` 行(Annotated dict + `_merge_metrics` 求和 reducer;五计数器 compile_pass/fail_count、smoke_pass/fail_count、rework_rounds;分支只报增量)。已对照 state.py:`METRIC_KEYS` 五键 + delta reducer + 主管 rework_rounds。
- §1.1 拓扑:route 映射 `finish|fail → END` 拆为 `finish → END` / `fail:* → w6_fail`;图后补 w6_fail 节点行;新增"失败收尾(设计 §8)"要点(agent.py::fail_package_node → `deliverable-failed-<ts>.zip`,内容 = 逐未交付子任务已有产物 + compile_errors + 审查裁决含 Minor + 原因 + records/status.json 记 spec_version + 冻结 spec 快照)。已对照 agent.py route/`fail_package_node`/w6_fail 注册与 package.py `build_failed`。
- §6 场景表:
  - #7 全局返工预算耗尽:原"输出 TodoList 摘要"→ w6_fail 未完成包(替代原摘要)。
  - #8 LLM 结构化输出失败:补 JSON 重试(parsed=None 且无 tool_calls → 同输入重试 1 次共 2 次尝试,仍败 → None → 确定性骨架降级;与工具 2 回合上限正交)。已对照 loader.py。
  - 新增 #27 反馈端点行(POST /tasks/{id}/feedback → 经验库 `propose("DEPLOY", sha256(reason)[:12], …)`,proposed 态签名去重,沉淀失败不阻塞,SSE 发 feedback 事件;404/401)。已对照 api.py `record_feedback`。
- §4.2 绑定形态:补畸形 JSON 重试(v1.10)。

### 3. docs/kingdee-plugin-agent/project.md

- §5.1:164 → 212(CHANGELOG v1.12.0);里程碑表新增 v1.9~v1.12 四行(时间预算+版本冻结 / P2 五项:metrics+otel+失败收尾包+JSON 重试+records 接线+.env / 冒烟链路 form_id+DLL+反馈端点+--env / 下发模板验收标准+上限字段)。
- §5.2 债务:``--env` 未消费` → `部分消费(v1.11:记录进 requirement_spec + state.environment["env_name"],未做环境级差异化)`,与 tech.md §11 / CLAUDE.md 债务措辞对齐(均已核实 cli.py:59)。
- §6 后续规划:`--env` 只进 requirement_spec → 亦进 environment["env_name"](v1.11)。

### 4. agents/kingdee_plugin_agent/CLAUDE.md

- api.py 文件职责行:补反馈端点 POST /tasks/{id}/feedback(经验库 DEPLOY 通道,沉淀失败不阻塞)。
- 常用操作:新增"可观测与指标"条目(metrics 随 State 统计 + delta reducer;otel spans 三处:kingdee.supervisor.decide(仅动作类型)/ kingdee.worker.<name>(subtask_id/plugin_type/status)/ kingdee.w5.compile_round(round/success),全低基数遵循 OBS-CORE-003,无用户自由文本进 span;api.py 启动 init_otel)。已对照 base.py/supervisor.py/w5_compile.py span 代码。
- 约束:新增"LLM 畸形输出重试"规则(重试 1 次共 2 次尝试,仍败 → 确定性骨架降级;与 load_skill 2 回合上限正交;改这里看 loader.py docstring 与 `test_structured_with_skill_parse_failure_*`)。

### 5. docs/superpowers/specs/2026-08-08-kingdee-plugin-agent-design.md

- §8 错误处理表按实现修订(3 行 + 同表 1 行附加):
  - 金蝶 API 连不上:降级纯文本模式 → 实际 BLOCKED → failed(降级纯文本模式未实现,P2);429 行同步补"重试 2 次(1s/2s),仍败 KingdeeApiUnavailable(无降级文本模式)"。
  - 审查退回超限(3 轮)问用户拍板 → 实际:子任务 max_rework 超限 → failed;全局预算耗尽 → fail → w6_fail 未完成包(问用户拍板未实现)。
  - 时间预算超限强制升级问用户 → 实际:图级总闸超限 → `fail:时间预算耗尽` → w6_fail 未完成包(强制升级未实现)。
  - **[附加]** LLM 结构化输出失败行(重试 2 次仍败报 BLOCKED 问用户)→ 实际:重试 1 次共 2 次尝试,仍败 → None → 确定性骨架降级(不报 BLOCKED)。审计清单未单列此行,但同属 §8 表按实现修订范围,已同步。
  - 全局返工预算超限/编译超限/冒烟失败/编译服务不可用/版本冻结行:顺带按实现措辞校准(内容与审计结论一致,无新增行为断言)。
- §3:w5"失败退回 w3/w4"→"退回 w3(needs_rework 恒映射 w3)";**[附加]** 相邻 w5.5 行"验证失败退回 w5/w3"同步改为"退回 w3"(同一机制,STATUS_TO_WORKER 恒映射,tech.md §2.3 佐证)。
- §6:API 参考库行"过滤命名空间/FormId"标注未实现(P2);§6.2 版本标注追加实现状态(当前 filter 仅 `{key: value}` 相等匹配,已接线 plugin_type 过滤/经验库签名去重;版本/命名空间标注未落地)。已对照 rag.py / w2_design.py(无 namespace/version 过滤代码)。
- §9 指标:标注实现状态 —— metrics 随 State 统计已落地(v1.10 五计数器),otel 仅打 span(低基数,OBS-CORE-003),指标数值 otel 上报未实现(P3)。
- §12:compose 三服务标注实现状态 —— 实际只启用 compile-service(api 注释态、RAG 存储未挂载,Plan C 后未更新),本地起 API 用 `uvicorn ...:create_app --factory`;**[附加]** 同节可观测行"指标随 State 统计上报"补"(指标 otel 上报未实现,P3)"以与 §9 一致。

### 6. agents/kingdee_plugin_agent/skills/requirement-clarify/SKILL.md

- 核心规则 #3"元数据驱动"追加实现状态注记:为设计目标(w1 接 KingdeeApiClient 后启用),当前 w1 未接线(已核实 w1_requirement.py 无 KingdeeApiClient 引用),选项由 LLM 按模板知识 + 确认摘要兜底;接真实环境时先接线再启用。

## 验证方式

- 212 项:`pytest tests/ --collect-only -q` 实测 212(kingdee 194 + sentiment 14 + eval 4),与 CHANGELOG v1.12.0"全套 212 项"一致。
- 其余每条均 grep/读码核对:agent.py(route/w6_fail/fail_package_node/_send_payload metrics 快照)、state.py(METRIC_KEYS/_merge_metrics)、loader.py(重试 2 次尝试)、api.py(POST /tasks/{id}/feedback、record_feedback、create_task payload 的 `env` 键)、cli.py(--env → environment["env_name"])、package.py(build_failed 内容)、base/supervisor/w5_compile(三个 span 名与低基数属性)、w1_requirement.py(无 API client)、rag.py(无 namespace/version 过滤)。

## 变更文件

- README.md
- docs/kingdee-plugin-agent/tech.md
- docs/kingdee-plugin-agent/project.md
- agents/kingdee_plugin_agent/CLAUDE.md
- docs/superpowers/specs/2026-08-08-kingdee-plugin-agent-design.md
- agents/kingdee_plugin_agent/skills/requirement-clarify/SKILL.md
- docs/superpowers/plans/docs-sync-report.md(本报告)

未动:代码、CHANGELOG.md(已至 v1.12.0)、manual.md(已同步)、.env.example。

## 关注点

1. **审计清单外的小幅校准**(均同类同表,已在上文标注 [附加]):design §8 的 LLM JSON 行、§3 w5.5 退回映射行、§12 可观测行。若要求严格只动清单,可单独 revert 这三处,但会留下同表/同机制的前后矛盾。
2. README 目录树沿用既有惯例把 sentiment 目录写成 `sentiment-query-agent/`(实际目录为 `sentiment_query_agent/`),未在本波修正(非本次范围)。
3. tech.md §9 原"test_kingdee_agent.py(86 项)"等按文件计数的旧数字已移除,改为按能力描述 + 全量 212,避免数字再次失同步。
