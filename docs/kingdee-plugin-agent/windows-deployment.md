# kingdee-plugin-agent Windows 编译服务部署手册

> 在金蝶服务器本机或同网段 Windows 机器上部署**真实 msbuild 编译服务**(compile_service)。
> 本文是**完整可执行**的部署手册:按步骤执行即可让 w5 编译环节接入真实金蝶 BOS 编译,
> 三类型样例(bill/service/list)已在本文所述环境真实编译通过并产出 DLL(E2E 门达成,
> 2026-08-09)。
>
> 面向对象:有金蝶服务器环境 Windows 管理权限的部署人员。技术背景见 [tech.md](tech.md) §8.2。

## 1. 适用场景与拓扑

```
Ubuntu agent(LLM + 主管图) ── COMPILE_SERVICE_URL ──► Windows 编译服务(uvicorn :8000)
                                                              │
                                                              ├─ .NET 4.8 DevPack(参考程序集)
                                                              └─ compile_service\build\references\*.dll(金蝶 BOS DLL)
```

- **为什么用 Windows 原生部署**:金蝶 BOS 为 .NET Framework 4.x,Framework MSBuild + Developer Pack
  参考程序集即可编译,无需安装 Visual Studio(旧式 csproj,ToolsVersion 4.0);Linux/Windows 容器方案保留为备选,
  Linux 容器内 mono 兼容性未验证。
- **编译服务与 agent 可分离部署**:agent 侧只要 `COMPILE_SERVICE_URL` 指向本服务即可(见 §9)。

## 2. 前置条件

| 项 | 要求 |
|---|---|
| 操作系统 | Windows Server 2016+(E2E 门验证环境)或同网段 Windows 10/11 |
| 网络 | 编译服务端口(默认 8000)对 agent 所在机器可达;Windows 防火墙放行入站 |
| Python | 3.10+(与项目一致) |
| .NET | .NET Framework 4.8 Developer Pack(§4) |
| 金蝶 DLL | 从金蝶服务器 WebSite\bin 拷贝(§5,授权注意) |
| 代码 | git clone agentStore 仓库(§3) |

## 3. 安装 Python 环境 + 拉取代码

在 Windows 机器上:

```bat
:: 1) 安装 Python 3.10(勾选 Add to PATH),然后:
git clone https://github.com/sunweini/agentStore.git E:\kingdee\agentStore
cd /d E:\kingdee\agentStore
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt          :: fastapi/uvicorn 等编译服务依赖
```

> 仓库可放**任意盘符/目录**(示例用 E:\kingdee\agentStore):部署脚本全部用 `%~dp0` 相对定位 + 环境变量,
> 不依赖固定盘符;schtasks 的 `/tr` 与 SYSTEM 账户 python 例外,需绝对路径(§7)。
> 若机器无法访问外网 pip,先在能联网的机器 `pip download -r requirements.txt -d wheels\` 再 `pip install wheels\*.whl`。

## 4. 安装 .NET Framework 4.8 Developer Pack

编译目标 `v4.8`(TARGET_FRAMEWORK 默认)需要 **Developer Pack**(提供参考程序集);
运行时版(.NET Framework 4.8 Runtime)不够,msbuild 会报找不到目标框架引用。

```bat
:: 下载并安装 .NET Framework 4.8 Developer Pack(约 100MB,微软官网)
:: https://dotnet.microsoft.com/download/dotnet-framework/net48  → Developer Pack
:: 安装后验证参考程序集存在:
dir "C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8"
```

> 无需安装 Visual Studio:Framework MSBuild 自带(§6),旧式 csproj 兼容。

## 5. 准备金蝶 BOS DLL(WebSite\bin)

金蝶云星空服务器安装目录(通常 `C:\Program Files (x86)\Kingdee\K3Cloud\WebSite\bin`)下有全部 BOS 运行时 DLL。
编译仅需**引用程序集**(编译期),拷到编译服务机器的 `compile_service\build\references\`:

```bat
:: 方式 A(推荐):fetch_kingdee_dlls.ps1 自动拷贝(核心 4 + 常用扩展,或 -All 全量)
::   -SourceDir 显式指定金蝶 bin;缺省自动探测常见安装路径;
::   KINGDEE_BIN_DIR 环境变量可替代自动探测提供源目录(-SourceDir 优先于 env)
set KINGDEE_BIN_DIR=C:\Program Files (x86)\Kingdee\K3Cloud\WebSite\bin
powershell -ExecutionPolicy Bypass -File compile_service\fetch_kingdee_dlls.ps1

:: 方式 B(手工拷贝):
mkdir E:\kingdee\agentStore\compile_service\build\references
cd "C:\Program Files (x86)\Kingdee\K3Cloud\WebSite\bin"
copy Kingdee.BOS.dll          E:\kingdee\agentStore\compile_service\build\references\
copy Kingdee.BOS.Core.dll     E:\kingdee\agentStore\compile_service\build\references\
copy Kingdee.BOS.App.dll      E:\kingdee\agentStore\compile_service\build\references\
copy Kingdee.K3.Core.dll      E:\kingdee\agentStore\compile_service\build\references\
:: 插件用到更多 BOS 命名空间时按需补充(如 Kingdee.BOS.Core.Metadata、Kingdee.BOS.Util 等均在 WebSite\bin)
:: 三类型模板编译实测仅需以上核心 4 个 + 自动补的 BOS 同目录 DLL,缺哪个按 §10.4 补
```

> ⚠️ **授权注意**:金蝶 BOS DLL 为**商业软件**,拷贝仅限内部编译使用,须确认金蝶授权范围,
> **勿公开分发、勿提交仓库**(`compile_service/build/references/` 当前仅 .gitkeep 占位,保持空目录入库、DLL 只放本地)。
> DLL 就位目录 = 服务端 `REFS_DIR` 缺省值(代码相对 `compile_service/build/references`,§6 可覆盖)。

## 6. 编写 start_compile.bat 并启动

新建 `compile_service\start_compile.bat`(内容如下,按实际路径微调):

```bat
@echo off
rem kingdee 编译服务启动脚本(Windows 原生部署;幂等,可被 schtasks 重复触发)
rem %~dp0 = 脚本所在目录(compile_service\):所有路径相对定位,仓库换盘符/目录无需改脚本
setlocal
cd /d %~dp0..
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat

rem 已监听 8000 则退出(保活任务重复触发不重复起服务)
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [%date% %time%] compile service already running on 8000, skip
    exit /b 0
)

rem ---- 真实 msbuild 后端必配 ----
set COMPILE_SERVICE_REQUIRES_DLLS=1
set REFS_DIR=%~dp0build\references
set TARGET_FRAMEWORK=v4.8
rem MSBUILD_PATH 可选:显式指定 msbuild;缺省自动探测(PATH 的 msbuild(VS 环境) → Framework 自带兜底)
rem set MSBUILD_PATH=C:\Windows\Microsoft.NET\Framework64\v4.0.30319\MSBuild.exe
rem FRAMEWORK_MSBUILD_PATH 可选:覆盖 Framework 自带兜底路径(系统盘非 C: 或 MSBuild 版本不同时用)
rem set FRAMEWORK_MSBUILD_PATH=D:\Windows\Microsoft.NET\Framework64\v4.0.30319\MSBuild.exe
rem COMPILE_ARTIFACT_DIR 可选:编译产物 DLL 留存目录(缺省 仓库根\data\kingdee-compiled,代码相对)
rem set COMPILE_ARTIFACT_DIR=%~dp0..\data\kingdee-compiled

python -m uvicorn compile_service.server:create_factory --factory --host 0.0.0.0 --port 8000 >> "%~dp0..\uv.log" 2>&1
```

启动:

```bat
cd /d E:\kingdee\agentStore
compile_service\start_compile.bat
:: 前台会阻塞(日志进 仓库根\uv.log,由 %~dp0 相对定位,不依赖盘符);验证:
curl http://localhost:8000/health     :: {"status":"ok"}
```

环境变量说明(服务端全部 env 可配 + 代码相对默认,**零硬编码部署路径**):

| 变量 | 值 | 说明 |
|---|---|---|
| `COMPILE_SERVICE_REQUIRES_DLLS` | `1` | 走真实 MsbuildCompiler;不带此变量 = mock 后端(仅开发/CI,不当质量门) |
| `REFS_DIR` | `%~dp0build\references`(代码相对默认 `compile_service\build\references`) | 金蝶 DLL 目录;为空 → 服务**启动即失败**(报"DLL 未到位") |
| `TARGET_FRAMEWORK` | `v4.8` | 编译目标框架,需 Developer Pack;低于金蝶 DLL 框架会 MSB3274/3275(§10.4) |
| `MSBUILD_PATH` | 可选 | 显式指定 msbuild;缺省自动探测:PATH 的 msbuild → Framework 自带兜底;后端直接读该 env(不经 server 参数) |
| `FRAMEWORK_MSBUILD_PATH` | 可选 | 覆盖 Framework 自带兜底路径(缺省 `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\MSBuild.exe`;系统盘非 C: 时设置) |
| `COMPILE_ARTIFACT_DIR` | 可选 | 编译产物 DLL 留存目录(缺省 仓库根 `data\kingdee-compiled`,代码相对,不随启动目录漂移) |

> **端口/监听地址(PORT/HOST)**:由 uvicorn 启动参数控制(`--host 0.0.0.0 --port 8000`),服务**不读** `PORT`/`HOST`
> 环境变量;需要环境变量驱动就在 bat 里 `set PORT=8088` 后改 `--port %PORT%`。
> 换端口(金蝶常占 8000):改 bat 的 `--port 8088`,agent 侧 `COMPILE_SERVICE_URL` 同步(§9)。

## 7. schtasks 计划任务保活

系统重启后自动拉起(建议配合 §6 bat 的幂等检查,重复触发不会起双服务):

```bat
schtasks /create /tn "kingdee-compile-service" /tr "cmd /c E:\kingdee\agentStore\compile_service\start_compile.bat" /sc onstart /ru SYSTEM /rl highest /f
schtasks /run /tn "kingdee-compile-service"     :: 立即触发一次(验证)
schtasks /query /tn "kingdee-compile-service"   :: 查看状态/上次结果
:: 删除:schtasks /delete /tn "kingdee-compile-service" /f
```

> SYSTEM 账户下 `python` 需在 PATH 中(安装 Python 时勾选 Add to PATH 对 SYSTEM 生效,或把 bat 里的 `python` 换成绝对路径 `E:\kingdee\agentStore\.venv\Scripts\python.exe`)。若用普通用户运行,`/sc onlogon` + `/ru <域\用户>` 替代 onstart。

## 8. 验证(health + 真实编译测试样例)

```bat
:: 1) 健康检查
curl http://localhost:8000/health          :: {"status":"ok"}

:: 2) 真实编译测试(bill 模板渲染占位符的最小样例,与 E2E 门同源)
::    代码含花括号,cmd 转义麻烦 —— 建议存 JSON 文件再提交:
::    sample-bill.json 内容:
::    {"project_name": "sample-bill", "code": "using System; using Kingdee.BOS.Core.Bill.PlugIn; using Kingdee.BOS.Core.DynamicForm.PlugIn.Args; using Kingdee.BOS.Core.Metadata; using Kingdee.BOS.Util; namespace Sample { public class BillSample : AbstractBillPlugIn { public override void OnLoad(EventArgs e) { base.OnLoad(e); } public override void AfterDoOperation(AfterDoOperationEventArgs e) { base.AfterDoOperation(e); } } }"}
curl -X POST http://localhost:8000/compile -H "Content-Type: application/json" --data-binary @sample-bill.json
:: 期望:{"success": true, "dll_path": "...\\data\\kingdee-compiled\\sample-bill\\Plugin.dll", "errors": [], ...}
:: success=false 时看 errors 列表与 仓库根\uv.log(§10)

:: 3) 拉取 DLL 验证产物可下载
curl -o Plugin.dll http://localhost:8000/dll/sample-bill
```

**mock vs 真实判别**(验证别被 mock 后端骗了):

| 判别点 | mock 后端(无 COMPILE_SERVICE_REQUIRES_DLLS=1) | 真实 msbuild 后端 |
|---|---|---|
| 启动 | 正常启动 | references 为空 → **启动即失败**(CompileUnavailableError"DLL 未到位") |
| `POST /compile` 的 `dll_path` | 空串 `""` | 非空留存路径 |
| `GET /dll/<project>` | 404「后端未配置 DLL 留存」 | 200 二进制 |
| 错误码 | 预设规则表(如 CS9990,不当质量门) | 真实 msbuild 输出(CS/MSB 系列) |

## 9. 与 Ubuntu agent 连接

agent 机器 `.env`:

```ini
COMPILE_SERVICE_URL=http://<windows-机IP>:8000
# 编译侧已真实验证;KD_* 4 项仍为 agent 全流程门槛(元数据/冒烟侧),按需配置
```

验证连通:

```bash
curl http://<windows-机IP>:8000/health    # {"status":"ok"}
```

> Windows 防火墙:入站规则放行 8000 端口(或进程 uvicorn);agent 侧先 `ping` / `telnet <ip> 8000` 排除网络问题。

## 10. 故障排查

### 10.1 服务起不来 / 编译 500

先看日志:start_compile.bat 重定向到 **`仓库根\uv.log`**(手工前台跑时直接看控制台)。常见三类:

1. **缺 Developer Pack**:msbuild 报找不到参考程序集/`TARGETFRAMEWORK` 相关 → 装 §4 DevPack。
2. **references 缺 DLL**:启动即报"金蝶 BOS DLL 未提供,真实编译不可用" → 按 §5 拷贝。
3. **端口被占**(金蝶 WebAPI 常占 8000):`netstat -ano | findstr :8000` 看占用进程 → 换 `--port`(§6)+ agent 侧 COMPILE_SERVICE_URL 同步。

### 10.2 msbuild 找不到

后端探测顺序:`MSBUILD_PATH` 环境变量(显式)→ PATH 的 msbuild(VS 环境)→
`FRAMEWORK_MSBUILD_PATH`(覆盖兜底路径)→ `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\MSBuild.exe`
(Framework 自带,一般必在)→ 退化 "msbuild"。
若仍报找不到:确认安装的是 Developer Pack 而非仅 Runtime(§4);`MSBUILD_PATH` 显式指到 msbuild.exe 并确认路径存在;
系统盘非 C: 时用 `FRAMEWORK_MSBUILD_PATH` 指向本机 Framework 目录;编译日志 仓库根\uv.log 会打印 msbuild 调用。

### 10.3 编译通过但缺类型/引用被跳过(warning MSB3274 / MSB3275)

金蝶 BOS DLL 是 **.NET 4.8**,当 `TARGET_FRAMEWORK` 低于引用程序集框架时,msbuild 只发 warning
**MSB3274/3275 并静默跳过引用** —— 编译"成功"但编出来缺类型(或后续 CS0246 找不到类型)。
修复:`TARGET_FRAMEWORK=v4.8`(start_compile.bat 已默认;改后**重启服务**)。经验库有对应种子(MSB3274/MSB3275)。

### 10.4 编译报 CS0246 找不到 Xxx 类

- 缺 `using System;` 等命名空间 → 补 using(E2E 修复之一,模板已带)。
- 引用的类型在未拷贝的 DLL → 按 §5 补充拷贝(参考程序集与 WebSite\bin 一一对应)。
- 基类/事件参数命名空间:bill 的 `AfterDoOperationEventArgs` 在 `Kingdee.BOS.Core.DynamicForm.PlugIn.Args`;
  service 基类 `AbstractOperationServicePlugIn` 在 `Kingdee.BOS.Core.DynamicForm.PlugIn`;模板已正确(E2E 反射验证)。

### 10.5 服务好了但 agent 仍报编译 BLOCKED

- agent 侧 `COMPILE_SERVICE_URL` 是否指向本机(非 localhost);防火墙/网络是否可达(§9)。
- 服务日志确认请求真的到达(仓库根\uv.log 有访问行)。

## 11. 注意事项

- **授权合规**:金蝶 DLL 仅限内部编译使用,确认授权范围,勿公开分发、勿提交仓库。
- **日志轮转**:仓库根\uv.log 会持续增长,建议定期清理或按天改名(如用批处理 + schtasks 定时轮转)。
- **references 增删 DLL 后需重启服务**(后端构造时一次性读取)。
- **编译时间**:msbuild 首次冷启动较慢(超时 180s),预热后单次编译数秒级。
- **agent 侧无需金蝶 DLL**:编译只发生在编译服务侧,agent 机器不需要安装 .NET/金蝶环境。
