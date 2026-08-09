# E2E 里程碑文档同步波 — 交付报告

日期:2026-08-09
范围:kingdee-plugin-agent 真实环境 E2E 门(三类型样例编译通过)后的 文档/skill/种子 同步。
对应代码已先落地(commit 30561ab 旧式 csproj / b706e8d DLL persist / 51e5468 三类型模板真实编译修复),本波为文档同步 + 本次一并补录 CHANGELOG。

## 逐项变更

### 1. CHANGELOG.md — 新增 v1.13.0

条目覆盖:旧式 csproj 兼容 Framework MSBuild(msbuild_path 探测 + target_framework 可配 + 180s 超时)/ DLL persist 时序修复 / 三类型模板真实编译修复(using System、AfterDoOperationEventArgs 命名空间、删除 K3.Core.ServiceHelper 假引用)/ **E2E 门达成**(Windows Server 2016 + 金蝶 WebSite\bin DLL + .NET 4.8 DevPack + Framework MSBuild)/ 文档与种子同步。沿用既有版本条目风格(新增功能/修复/测试/文档)。

### 2. seed/compile_errors.json — 新增 3 条真实踩坑种子(7 → 10 条)

- `MSB3274`(目标框架低于引用程序集,引用被跳过 → TARGET_FRAMEWORK 提到 v4.8)
- `MSB3275`(传递引用不兼容 → 同 MSB3274)
- `CS0246`(file_pattern=EventArgs:缺 using System → 补 using System;)
- 签名去重不冲突:新 CS0246 的 file_pattern=EventArgs 与既有 CS0246("")签名不同。
- 测试断言更新:`tests/test_rag.py` 与 `tests/test_kingdee_agent.py` 的 seed_load 断言 `>= 7` → `>= 10`(注释同步);`tests/test_kingdee_agent.py::test_seed_load_cli_main` 幂等断言(二次 0)不受影响。

### 3. agents/kingdee_plugin_agent/CLAUDE.md

「接真实金蝶环境」更新:编译服务改为 **Windows 原生部署**表述 —— 前置(Python 3.10 + .NET 4.8 DevPack)、金蝶 DLL 来源 **WebSite\bin** 拷到 `compile_service\build\references\`(商业软件授权注意)、`start_compile.bat` 环境变量(`COMPILE_SERVICE_REQUIRES_DLLS=1` + `REFS_DIR` + `TARGET_FRAMEWORK=v4.8`;`MSBUILD_PATH` 可选,自动探测 PATH → Framework 兜底)、schtasks 保活;注明 **E2E 门已达成** 及完整手册指针。

### 4. docs/kingdee-plugin-agent/manual.md

- §1.1 可选配置补 `TARGET_FRAMEWORK=v4.8`(Windows 部署必配)。
- §1.2 种子输出 "新增 7 条" → "新增 10 条"。
- §1.3 改为 Windows 原生部署为主(嵌入 start_compile.bat 内容 + 链接 windows-deployment.md),Docker 方式降级为备选(未验证)。
- §5 FAQ 新增 Q10(端口被金蝶占 → 换端口+COMPILE_SERVICE_URL)、Q11(编译 500 → 看 E:\uv.log,三类常见原因)、Q12(MSB3274/3275 → TARGET_FRAMEWORK=v4.8);Q7/Q9 同步 E2E 门状态与 120s/180s 超时口径。
- §7 限制与未验证项:新增 "E2E 门(编译侧)已达成" 行,未验证项收敛为 WebAPI/冒烟侧 + Linux 容器。

### 5. docs/kingdee-plugin-agent/tech.md

- §5.3 种子 7 → 10 条。
- §8.2 msbuild 后端整段更新:旧式 csproj(ToolsVersion 4.0)理由、msbuild_path 探测、target_framework 可配 + MSB3274/3275 风险、180s 超时、DLL 字节在临时目录删除前读取;Dockerfile 条目补 "实际采用 Windows 原生部署,容器方案保留"。
- §6 #22 与 §10.2:msbuild 超时 120s → 180s。
- §9 E2E 门 未达成 → ✅ 已达成(注明环境)。
- §11 未验证项:移除 E2E 启动门与 Linux 容器兼容性(改注 Windows 原生部署)。

### 6. docs/kingdee-plugin-agent/project.md

- §5.1 里程碑表新增 "v1.13 E2E 门达成" 行(✅ 达成,注明环境);导语改 v1.9~v1.13 + E2E 门已达成。
- §5.2 待办重排:删除 "团队 DLL 到位 → 真实容器编译" 项(已达成);新增 "RAG 内容"(standards 未接真实资料);"真实金蝶环境联调" 聚焦 WebAPI 凭证(KD_*)+ 冒烟侧。

### 7. 设计文档 §13(2026-08-08-kingdee-plugin-agent-design.md)

- "里程碑 1 前置" 更新为 ✅ 已达成(2026-08-09,注明环境与配套产物,授权注意保留)。
- Linux 容器兼容性条目备注:实际采用 Windows 原生部署,容器方案保留未验证。

### 8. skill 措辞

- code-generator/SKILL.md:模板"均为团队验证过的基准" → "已真实环境编译验证的基准(三类型模板已在 Windows + 金蝶 BOS DLL 环境编译通过并产出 DLL)"。
- design-builder/SKILL.md:模板基准对照处补 "三类型模板已真实环境编译验证(Windows + 金蝶 BOS DLL)"。
- compile-fixer SKILL.md/errors.md **未改动**(保持纯方法论,具体错误已入种子 —— 按既定原则)。
- knowledge-steward/SKILL.md:种子 "7 条基准" → "10 条基准"(含真实环境 MSB3274/3275)。

### 9. 新增 docs/kingdee-plugin-agent/windows-deployment.md

Windows 编译服务部署手册(完整可执行):
- 前置:Windows Server 2016+ / Python 3.10 / git clone;§3 安装步骤(含离线 pip 提示)。
- §4 .NET Framework 4.8 Developer Pack(参考程序集,提供 v4.8 目标;运行时版不够)。
- §5 金蝶 DLL:从金蝶服务器 WebSite\bin 拷核心 DLL(Kingdee.BOS/Kingdee.BOS.Core/Kingdee.BOS.App/Kingdee.K3.Core + 按需补充)到 `compile_service\build\references\`;授权注意(商业软件,勿公开分发/入库,references 仅 .gitkeep 占位)。
- §6 start_compile.bat 完整内容(幂等端口检查 + 环境变量 + uvicorn 重定向 E:\uv.log)+ 环境变量表。
- §7 schtasks 计划任务保活(onstart/SYSTEM + 注意事项)。
- §8 验证:health + 真实编译测试样例(bill 模板渲染占位符,JSON 文件方式避免 cmd 转义)+ DLL 拉取 + **mock-vs-real 判别表**。
- §9 与 Ubuntu agent 连接(COMPILE_SERVICE_URL + 防火墙)。
- §10 故障排查(端口占用/500 日志/msbuild 找不到/MSB3274-3275/CS0246 命名空间)。
- §11 注意事项(授权/日志轮转/重启语义/预热/agent 无需 DLL)。

## 验证

- `.venv/bin/python -m pytest tests/ -q`:**212 passed**(基线 212,种子断言更新后仍全绿;幂等二次灌入 0 通过)。
- seed_load 幂等契约:`test_seed_load_idempotent`(`>= 10` 且二次 0)与 `test_seed_load_cli_main`(打印 "种子灌入完成:新增 10 条")均通过。

## 关注点

1. **start_compile.bat 未入库**:手册内嵌 bat 内容(§6),仓库 `compile_service/` 下暂无该文件 —— 部署时按手册创建即可;若后续希望直接跑,可考虑把 bat 提交进仓库(本波按指令仅文档)。
2. **E:\uv.log 为真实环境路径**:FAQ/手册中的日志路径来自实际部署(Windows 编译服务日志重定向),其他部署环境需按实际路径调整。
3. **msbuild 后端超时 180s vs 客户端 httpx 120s 不一致**(代码现状):编译侧慢于客户端超时,极端冷启动可能客户端先超时;文档按现状如实标注(§10.2/tech §6#22),未改代码。
4. **历史报告文件**(plans/*-report.md)中的 "7 条种子" 等为历史记录,未改;live 文档(tech/manual/skill/测试注释)已全部更新为 10 条。
