# 环境类错误升级报告(env-error-escalation)

日期:2026-08-10
分支:main(基线 6477bc9)
范围:kingdee-plugin-agent 编译修复闭环的三个知识消费缺口(Gap A/B/C)

---

## Gap A — 环境类错误不再空转修复轮次

**问题**:CS1056 / MSB4067 / MSB3274 / MSB3275 / TimeoutExpired 是编译服务配置问题(环境类),非代码问题。原 w5 把它们从经验库检索后照常走"LLM 改代码 → 重编"循环,最多空转 5 轮再扣预算,修不了环境。

**实现**(全部落码):
1. `agents/kingdee_plugin_agent/seed/compile_errors.json`:13 条种子全部显式标注 `category` —— 5 条环境类(CS1056 / MSB4067 / MSB3274 / MSB3275 / TimeoutExpired)= `"env"`,8 条代码类 = `"code"`(一致默认,不用"缺省即 code"隐式语义)。
2. `common/rag.py`:`ExperienceStore.propose` 新增可选参数 `category: str = "code"`(向后兼容 —— w7/DEPLOY/ARTIFACT 三处既有调用全部位置参数,不受影响),category 随元数据入库,经 `search_related` 原样透传给 w5。
3. `agents/kingdee_plugin_agent/seed/seed_load.py`:灌入元数据带 `item.get("category", "code")`,种子与 propose 通道行为一致。
4. `agents/kingdee_plugin_agent/graph/workers/w5_compile.py`:
   - `_retrieve_fix` 返回 `str | None`:遍历本轮错误,命中 `category == "env"` 的条目时聚合提示串(首 2-3 条,跨错误累计;代码类命中只附注不进提示)。经验库故障依旧吞异常返回 None 不阻断。
   - `_execute`:失败轮 `_retrieve_fix` 返回非 None → 立即 `{"status": "BLOCKED", "evidence": "编译环境问题", "concerns": <运维提示串>}`。不进 `_llm_fix`、不计 `compile_fail_count`、不扣 `rework_budget_left`、编译客户端只调 1 次即停。
   - 模块 docstring 同步说明升级语义。

**测试**(新增 4 个,`tests/test_kingdee_agent.py`):
- `test_compile_env_error_blocked_no_budget`:env 命中 → BLOCKED + 运维提示含 CSC_TOOL_PATH;client.calls == 1;capture-LLM 未被调;预算 3→3;compile_fail_count == 0;experience 附注仍在。
- `test_compile_env_error_multiple_hits_aggregated`:CS1056(2 条 env 命中)+ CS0103(代码类)混批 → 提示含前 2 条 env 提示;代码类命中仍附注到对应错误条目。
- `test_compile_code_category_hit_normal_path`:category="code" 命中 → 正常修复循环不变(5 轮 + 扣 1 预算)。
- `test_rag.py::test_propose_carries_category`:propose 默认 code / 显式 env 随元数据入库并透传;`test_seed_load_idempotent` 补 category 元数据断言(CS1056=env,CS0246=code)。

## Gap B — w5 prompt 摘要注入(load_skill 兜底)

**问题**:w5 的 LLM 只在主动调 `load_skill('compile-fixer')` 时拿到方法论,不调就完全没有 —— 环境类错误可能被当成代码错误空改。

**实现**:
- `w5_compile.py` 新增模块常量 `COMPILE_FIXER_SUMMARY`(2-3 行:方法论在 compile-fixer skill(load_skill 获取完整版);环境类错误(C# 6 语法需 Roslyn / MSBuild 配置 / 目标框架不匹配 / 编译超时)不要修代码,报告 BLOCKED 提示运维)。`_llm_fix` 系统消息改为 `prompt + "\n\n" + COMPILE_FIXER_SUMMARY + SKILL_HINT` —— 保持既有 SKILL_HINT 结构,摘要插在其前。
- `skills/loader.py` `_AVAILABLE_SKILLS["compile-fixer"]` 摘要同步补一句环境类说明(摘要层与 w5 注入内容一致,防语义漂移)。
- 无 `{}` 花括号内容,不触 ChatPromptTemplate f-string 转义问题(dev-standards §7.2)。

**测试**:`test_w5_system_prompt_contains_compile_fixer_summary`(capture-LLM 模式,与既有测试同型)—— 每轮系统消息含 "compile-fixer"、"环境类错误"、"BLOCKED"、"C# 6 语法需 Roslyn"。

## Gap C — dev-standards §7 补 Windows 远程运维经验

`docs/dev-standards.md` 新增 **§7.6「Windows 远程部署与运维(kingdee 编译服务实测)」**,9 条,遵循 §7 既有风格(加粗要点 + 现象/对策):
1. scp 多文件必须逐个指定目标路径(`scp a b host:dir/` 会把 b 当目标路径);
2. ssh 传参三层转义 → PowerShell `-EncodedCommand`(base64 UTF-16LE)规避;
3. 中文输出乱码 → `[Console]::OutputEncoding = [Text.Encoding]::UTF8`;
4. Server 2016 无 Add-WindowsCapability(2019+ 才有)→ OpenSSH 用 MSI;
5. uvicorn factory 必须显式 `--factory`(自动检测在 Server 2016 报 create_factory() takes 0 args);
6. schtasks 保活后台服务 + 环境变量经 bat 文件传递(schtasks 不继承 SSH env);
7. 改代码后 taskkill 全杀再重启(端口占用导致旧进程继续服务);
8. PowerShell 5.1 无 BOM UTF-8 读乱码 → 注释用英文或带 BOM;
9. 金蝶服务器 8000 端口被占(默认 WebSite)→ 编译服务换端口。
§7 引言同步标注经验来源含 kingdee-plugin-agent。

## Also — 方法论文档 + CHANGELOG

- `skills/compile-fixer/SKILL.md` 新增「**环境类 vs 代码类(判别与升级)**」小节:两类判别标准、判别线索(写法与模板一致仍报语法错 / 换编译器配置后消失 / 与代码改动无关)、升级路径(BLOCKED + 运维提示,不计轮次不扣预算,LLM 不参与)。
- `skills/compile-fixer/references/errors.md` §四 修复纪律补第 7 条「环境类错误升级不修码」(保持纯方法论定位,无具体错误码映射)。
- `skills/knowledge-steward/SKILL.md` 检索路由表 experience 行补 env 命中升级注。
- `CHANGELOG.md` 追加 **v1.19.0**(行为变更 + 文档 + 测试)。

## 测试结果

- 定向:15 passed(env/summary/seed/propose 相关)。
- 全量:`pytest tests/ -q` → **272 passed**(基线 267 + 新增 5 个测试函数;2 warnings 为既有 CUDA/starlette 弃用警告)。

## 变更文件

| 文件 | 变更 |
|---|---|
| `agents/kingdee_plugin_agent/seed/compile_errors.json` | 13 条种子加 category(code/env) |
| `common/rag.py` | ExperienceStore.propose +category 参数(默认 code) |
| `agents/kingdee_plugin_agent/seed/seed_load.py` | 灌入元数据带 category |
| `agents/kingdee_plugin_agent/graph/workers/w5_compile.py` | env 命中 → BLOCKED 短路;COMPILE_FIXER_SUMMARY 注入;docstring |
| `agents/kingdee_plugin_agent/skills/loader.py` | compile-fixer 摘要补环境类说明 |
| `agents/kingdee_plugin_agent/skills/compile-fixer/SKILL.md` | 「环境类 vs 代码类」判别与升级 |
| `agents/kingdee_plugin_agent/skills/compile-fixer/references/errors.md` | 修复纪律第 7 条 |
| `agents/kingdee_plugin_agent/skills/knowledge-steward/SKILL.md` | 路由表 experience 行补 env 升级 |
| `docs/dev-standards.md` | §7.6 Windows 远程部署与运维(9 条) |
| `CHANGELOG.md` | v1.19.0 |
| `tests/test_kingdee_agent.py` | +4 测试 |
| `tests/test_rag.py` | +1 测试 + seed 断言扩展 |

## 关注点(Concerns)

1. **env 判定依赖经验库元数据**:已有 Chroma 库中旧条目(propose 通道、旧种子)无 category 字段 → `.get("category") == "env"` 为 False → 走代码类路径。**需 drop data/kingdee-rag 重灌种子**(或至少重跑 seed_load,幂等跳过已存在签名 —— 旧条目无 category,重灌不更新元数据,须删除后重灌)才能让种子 env 判定生效;w7 新沉淀默认 code,环境类条目需在 propose 时显式传 category="env"(当前 w7 代码未传 —— 运行期环境类错误目前只能靠种子命中)。
2. **BLOCKED 后主管侧语义**:env 升级复用既有 BLOCKED 态(退回 w3/w4 或问用户),未新增"ENV_BLOCKED"态 —— 减少图改动,但运维提示只在 concerns 里,下游若丢弃 concerns 会丢失修复指引。
3. **聚合上限 3 条**:跨错误只取前 3 条 env 提示,超出的错误类不进入提示(避免刷屏),但全部 env 命中仍附注到各自 compile_errors 条目。
4. **LLM 摘要与 skill 摘要双源**:w5 注入常量与 loader 摘要措辞略有不同,属有意为之(前者含 load_skill 指引与 BLOCKED 报告语义,后者为通用摘要层);若未来收敛单源,应统一从 loader 取。
