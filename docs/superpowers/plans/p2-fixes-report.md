# P2 修复报告(design-vs-code 审计 5 项)

日期:2026-08-09 | 分支:main(前 head 8548b85) | 范围:kingdee-plugin-agent P2-6/7/8/9/11,不动 P1/P3

测试基线:172 项全过 → 实现后 184 项全过(12 新增)→ 评审修复后 185 项全过(+1 新测试,otel 测试扩展断言)。

---

## 评审修复(2026-08-09,提交 "fix(otel/package): span 低基数 action + zip 条目 id 净化")

任务复核 1 Important + 2 Minors,全部修复:

- **Important — span action 高基数违规(OBS-CORE-003)**:`kingdee.supervisor.decide` span 原记录完整 action,`ask_user:<问题>` 的问题文本是用户输入/LLM 生成的高基数自由文本。修复:`span.set_attribute("action", action.split(":", 1)[0])` 只记动作类型(run/ask_user/finish/fail);核对其余新 span 均无用户派生文本(worker span 的 subtask_id 已过 ArtifactStore 白名单、plugin_type/status 为枚举值;编译轮次 span 的 round/success 为数值/布尔)。测试:`test_otel_spans_wired_without_collector` 追加 ask_user 带问题场景 —— 断言 span 属性只含 "ask_user",且**所有** span 属性值不含问题原文。
- **Minor — build_failed zip 条目 id 未净化**:`subtasks/<sid>/...` 的 sid 直接进 zip 条目路径,脏数据(../ 等)可致路径穿越。修复:复用 ArtifactStore 白名单模式 `^[A-Za-z0-9_-]+$`,非法字符替换为 "_"、空 id 兜底 "unknown"(产物保留,不丢弃)。测试:`test_build_failed_sanitizes_subtask_ids` 断言 "../evil" → "___evil"、"B1/x" → "B1_x"、空 id → "unknown",且无任何 ".." 条目。
- **Minor — 注入 builder 契约静默扩展**:`fail_package_node` 调用 `builder.build_failed`,只实现 build 的注入实例会静默漏掉失败收尾。修复:PackageBuilder 类 docstring 显式注明注入契约(必须同时实现 build 与 build_failed,缺失时 AttributeError 显式暴露,不做静默降级)。

---

## P2-6 可观测与指标(设计 §2/§9/§12)

### 实现

- **`TaskState.metrics` 计数通道**(`graph/state.py`):`Annotated[dict, _merge_metrics]`,键 = compile_pass_count / compile_fail_count / rework_rounds / smoke_pass_count / smoke_fail_count(`METRIC_KEYS` 常量)。
- **计数点**:
  - w5(`w5_compile.py`):编译通过 → `compile_pass_count += 1`;5 轮超限 → `compile_fail_count += 1`。
  - w5_5(`w5_5_smoke.py`):冒烟通过/失败 → `smoke_pass_count` / `smoke_fail_count`。
  - rework_rounds:主管(`agent.py::supervisor_node`)按返工事件数累计 —— 与预算扣减同源(w4 重审 + w5 超限 + w5_5 冒烟失败全覆盖),预算扣 1 = 返工 1 轮。
- **并行安全(实测发现并修复两个 LangGraph 通道陷阱)**:
  1. **共享引用原地改**:`_send_payload` 的 metrics 必须 `dict()` 拷贝 —— 并行 Send 分支共享同一 dict 引用时,worker 的 `+=` 原地改通道当前值,reducer 在此基础上再求和 → 重复累计(实测双任务 compile_pass_count=4 而非 2)。
  2. **Annotated 通道初始化不给 dataclass 默认值**(初始空 dict)→ `_as_state` 缺键补齐 0 + reducer setdefault 补齐,计数键始终完整。
  - 分支只上报**增量**(执行前后差值),跨多轮派发不重复累计;`_send_payload` 补 `metrics` 快照。
- **OTel span**(复用 `common/otel.py` 模式,低基数属性、无用户信息遵循 OBS-CORE-003):
  - `base.py::run`:worker 状态迁移 span `kingdee.worker.<name>`(subtask_id / plugin_type / status)。
  - `w5_compile.py`:每轮编译 span `kingdee.w5.compile_round`(round / success / unavailable)。
  - `supervisor.py::decide`(包装 `_decide`):span `kingdee.supervisor.decide`(action)。
  - `api.py::create_app` 启动时 `init_otel()`(与 sentiment api.py 同款;OTEL_ENDPOINT 未配置 → 空 provider,span 丢弃,本地无 collector 不阻塞)。

### 验证

- 单测:编译通过/超限计数、冒烟通过/失败计数、metrics 默认全 0。
- 图级:全链路含返工 → `rework_rounds==1`;并行双任务 → compile/smoke pass 各 2(增量合并不重复)。
- OTel:fake tracer(monkeypatch 模块级 get_tracer)断言三个 span 名 + 低基数属性;无 collector 不崩(既有全图测试经真实 no-op tracer 路径覆盖)。

---

## P2-7 失败收尾产物(设计 §8)

### 实现

- **`tools/package.py::build_failed`**:`deliverable-failed-<ts>.zip`(文件名标注失败态)+ `records/status.json`(status=failed / reason / spec_version / 冻结 spec 快照)+ 逐未交付子任务目录 `subtasks/<sid>/`(source/Plugin.cs、design.md、review.json、compile_errors.json、status.txt 带审查裁决);产物缺失自然跳过。
- **`agent.py` 新增 `w6_fail` 失败打包节点**:`route()` 里 `fail* → w6_fail → END`(finish 仍直接 END)。节点从产物库收集每个未交付子任务的 design.md / Plugin.cs / review.json(缺失容忍)+ `subtask.compile_errors`(编译超限 5 轮后的错误日志,已记在 subtask)+ review_verdict,`reason = state.action`;产出记入 final_deliverable + final_deliverables —— CLI/API 与正常交付包同一通道展示,失败也有可审计产物(原实现 fail 只有 TodoList 摘要)。
- 覆盖所有 fail 原因:返工预算耗尽 / 时间预算耗尽 / 存在失败子任务 / 主管判定;已 delivered 子任务不进未完成包。

### 验证

- 预算耗尽图运行 → zip 断言:records/status.json 的 reason == "fail:返工预算耗尽"、spec_version;subtasks/A1/compile_errors.json 含 CS0103(编译超限错误);design.md / Plugin.cs / review.json(含 Minor)内容进包。
- 时间预算耗尽(30min 总闸)→ 同样出未完成包,reason 可审计。
- 既有终态测试(fail action / todo 状态)不受影响。

---

## P2-8 LLM 畸形 JSON 重试(设计 §8:解析失败重试 2 次)

### 实现

`skills/loader.py::structured_with_skill`(工具绑定路径):`parsed=None 且无 tool_calls`(畸形 JSON / parsing_error)→ **同一份输入**重试 1 次(共 2 次尝试),仍失败返回 None → worker 既有确定性骨架降级。重试与工具 2 回合上限正交:工具回合后的结果直接返回(成功出 schema / 又调工具强制停止),不参与重试;2 回合工具上限不变。无 bind_tools 的 fake/脚本路径(单次 invoke)不动。

### 验证

- 失败 1 次 → 重试成功:结果返回(重试救回,不进骨架),输入一致(未掺失败响应)。
- 失败 2 次 → None;`len(seen)==2`(既有测试由 1 次断言更新为 2 次语义)。

---

## P2-9 交付包 records 接线(设计 §5.4/§12)

### 实现

`w6_package.py::_execute`:deliverable 增加 `design`(`_read_design`:design.md → `{"content": <正文>}`)与 `review`(`_read_review`:review.json → findings 列表,缺失/损坏容错为空);PackageBuilder 既有 `deliverable.get("design"/"review")` 写入路径已存在(核实),records/design.json + records/review.json 由此真实落内容 —— 原实现恒空 {}。w4 把**全部** findings(含 Minor)写入 review.json(已核实),Minor 意见因此自动进包。

### 验证

- design.md + review.json(含 Minor + Critical)真实内容进包断言;记录缺失时包仍可产出(空占位)。

---

## P2-11 .env.example 配置组

### 实现

`.env.example` 新增:

```
# ===== kingdee-plugin-agent:金蝶云星空环境 =====
KD_BASE_URL / KD_USERNAME / KD_PASSWORD / KD_DATA_CENTER(4 项硬门槛,注释说明 CLI/API 语义)
COMPILE_SERVICE_URL(缺省 http://localhost:8000)+ COMPILE_SERVICE_REQUIRES_DLLS / REFS_DIR(注释态)
KINGDEE_API_KEY(Web API 鉴权,缺省 401)
# ===== OpenTelemetry =====(OTEL_ENDPOINT 归入独立分组)
```

风格对齐既有 `# ===== 分组 =====` + 行内注释。manual.md §1.1 声明的 4 组配置 + 可选 DLLS/REFS_DIR 与 .env.example 逐一对应(已核对)。

---

## 验证与测试

- 全量:`pytest tests/ -q` → **185 passed**(172 基线 + 12 新增 + 1 评审修复新增),0 失败。
- 新增 12 项:metrics 5 项(w5 通过/超限、w5_5 通过/失败、默认 0、图全链路返工、并行合并)、otel 1 项、失败收尾包 2 项、records 接线 2 项、JSON 重试 2 项。
- 评审修复:新增 zip 条目 id 净化测试 1 项(非法 id 替换/兜底,无穿越);span action 低基数断言并入既有 otel 测试(ask_user 问题文本不进任何 span 属性)。

## 变更文件

- `agents/kingdee_plugin_agent/graph/state.py`(metrics + reducer + METRIC_KEYS)
- `agents/kingdee_plugin_agent/agent.py`(send 快照拷贝、supervisor rework_rounds、w6_fail 节点 + 路由、_as_state 补齐)
- `agents/kingdee_plugin_agent/graph/supervisor.py`(decide span,拆 _decide)
- `agents/kingdee_plugin_agent/graph/workers/base.py`(worker run span)
- `agents/kingdee_plugin_agent/graph/workers/w5_compile.py`(计数 + 编译轮次 span)
- `agents/kingdee_plugin_agent/graph/workers/w5_5_smoke.py`(冒烟计数)
- `agents/kingdee_plugin_agent/graph/workers/w6_package.py`(records 接线)
- `agents/kingdee_plugin_agent/skills/loader.py`(畸形 JSON 重试)
- `agents/kingdee_plugin_agent/tools/package.py`(build_failed)
- `agents/kingdee_plugin_agent/api.py`(init_otel)
- `agents/kingdee_plugin_agent/CLAUDE.md`(返工预算约束行更新:未实现 → w6_fail 未完成包)
- `.env.example`、`CHANGELOG.md`(v1.10.0)、`tests/test_kingdee_agent.py`(+12)

## 自审

- 指标口径已文档化:compile/smoke 按 w5/w5_5 每次执行结果计数(重工重跑会再计,= 轮次结果),rework_rounds 与预算扣减同源 —— 与设计 §9 "pass-rate / 返工轮次/任务 / 冒烟通过率" 一致。
- 失败打包节点对任何 fail 原因都出包(不限于预算两种)—— 一致性与可审计性优先,测试覆盖预算/时间两种指定场景。
- 未触碰 P1(编译服务 DLL 接线/元数据/RAG 内容)与 P3 项;P2-10(时间预算状态显示)不在本次范围。

## 关注点

1. **指标口径语义**:compile_pass_count 是"编译轮次结果数"非"子任务数" —— 重工重跑会重复计(与设计"编译通过/任务总数"有细微差异)。如需精确 pass-rate,后续可在主管层按子任务终态再归一,当前口径已足够观察趋势。
2. **fail 时 reviewer.json 为空的子任务**(如冒烟失败,审查已过)包内没有 review.json —— 已通过 status.txt 记录 review_verdict 兜底。
3. **OTel span 为手动埋点**(未接 langchain/LangGraph 自动 instrumentation),符合"复用 common/otel.py 模式 + 最小 span 创建"的 P2 范围;自动 instrumentation 属后续增强。
4. 全流程时间预算测试中 `started_at` 用 `time.time()-2000` 构造,与既有时间预算测试同模式(非 flaky)。
