# Windows 编译经验知识沉淀报告(CS1056 / MSB4067 / 编译超时)

日期:2026-08-10
范围:kingdee-plugin-agent Windows 真实环境编译里程碑(commit 1bfcf8f)产生的新踩坑 → 知识系统回填。
原则:具体错误映射 → experience-store 种子;方法论 → skills/文档;skill 文件不写静态错误码表。

## 沉淀条目

| 踩坑 | 证据(代码) | 沉淀位置 |
|---|---|---|
| CS1056「意外的字符$」:Framework csc 仅 C# 5,不认 C# 6+ 字符串内插 | `msbuild.py` CSC_TOOL_PATH → csproj 写 CscToolPath/CscToolExe=csc.exe;`server.py` env 接线 | 种子(CS1056)+ SKILL.md 编译环境要点 + windows-deployment §10.5 + manual Q13 |
| MSB4067「无法识别元素 <CscToolPath>」:属性写成 Project 直接子元素 | `msbuild.py` csc_tool 块包 `<PropertyGroup>` | 种子(MSB4067)+ §10.5 + manual Q13 |
| subprocess.TimeoutExpired:Roslyn 冷启动 + ~30 引用首次编译超 180s | `msbuild.py` timeout=300(180 → 300) | 种子(TimeoutExpired,note 型)+ SKILL.md + §10.5 + manual Q14 |
| MSB3274/3275(TARGET_FRAMEWORK 匹配)—— 既有种子核实 | `compile_errors.json` 已有 2 条,fix 均指向 TARGET_FRAMEWORK → v4.8,提及"引用被静默跳过" | 无重复,未新增 |

## 文件变更

1. `agents/kingdee_plugin_agent/seed/compile_errors.json`:+3 条(10 → **13**,签名
   `CS1056|`、`MSB4067|`、`TimeoutExpired|`),格式与既有条目一致(seed 源、语义化
   message、可执行 fix),seed_load 幂等签名去重天然兼容。
2. `agents/kingdee_plugin_agent/skills/compile-fixer/SKILL.md`:新增「编译环境要点」节
   (方法论层,零错误码 —— grep `CS\d{4}|MSB\d{4}` 0 命中,errors.md 纯方法论契约测试
   不受影响):无 VS 环境 = Framework MSBuild + 旧式 csproj;C# 6+ 语法必须 Roslyn
   (CSC_TOOL_PATH);目标框架 ≥ 金蝶 DLL 框架(否则引用静默跳过);首编冷启动慢(超时
   放宽 ≥300s);csproj 属性必须包 `<PropertyGroup>`。
3. `docs/kingdee-plugin-agent/windows-deployment.md`:故障排查新增 §10.5(CS1056 →
   CSC_TOOL_PATH;MSB4067 → PropertyGroup;编译超时 → 后端 300s + agent 侧 120s 误判
   提示),原 §10.5 → §10.6;§11 编译时间 180s → 300s。
4. `docs/kingdee-plugin-agent/manual.md`:FAQ 新增 Q13(CS1056)/Q14(编译超时);Q9 后端
   超时 180s → 300s;§1.2 种子输出「新增 10 条」→「新增 13 条」。
5. `CHANGELOG.md`:新增 v1.18.0(新增功能:Roslyn 支持说明 —— 代码已随 1bfcf8f 落地,
   本版补沉淀;经验沉淀:种子/skill/文档/FAQ 明细)。
6. `tests/test_rag.py`、`tests/test_kingdee_agent.py`:seed 断言注释同步 10 → 13
   (断言 `n1 >= 10` 本身不变,仍通过)。

## 验证

- 全量 `pytest tests/ -q`:**267 passed**(与基线一致,无新增测试 —— 纯沉淀变更,
  断言为 `n1 >= 10` 无需改动)。
- 种子 JSON 校验:合法,13 条;幂等测试(二次灌入 0)通过。
- SKILL.md 静态错误码扫描:0 命中(纯方法论契约保持)。

## 关注点

1. **agent 侧超时 < 后端超时(既有代码,本次仅文档提示)**:后端 subprocess 超时已放宽
   300s,但 agent 侧 `CompileClient` httpx 超时仍为默认 120s —— 首编耗时在 120~300s
   之间时 agent 会误判「编译服务不可用(超时)」(httpx.TimeoutException → BLOCKED)。
   文档已在 §10.5 / manual Q14 提示(先重试,持续误判调大 agent 侧超时),但未改代码
   —— 属部署参数调整,建议后续把 `CompileClient` 超时上限与后端对齐。
2. **TimeoutExpired 条目形态**:是 note 型种子(code=TimeoutExpired,非编译器错误码)。
   当前 w5 检索路径按 CompileError 的 code/message 检索,该条目主要服务知识库检索与
   人工运维,不会被 w5 编译错误自动命中 —— 已按任务要求以 note 型入库,不影响正确性。
3. **种子重灌提示**:种子文件变更后,线上 `data/kingdee-rag` 需要重跑一次
   `seed_load`(幂等签名去重,新增的 3 条会补齐)。
