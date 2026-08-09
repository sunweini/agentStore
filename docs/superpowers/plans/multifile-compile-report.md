# 多文件编译支持(multi-file compile)实现报告

日期:2026-08-09 | 分支:main | 基线 90f0bd1

## 1. 设计(向后兼容)

`POST /compile` 现接受两种形态(二选一,`project_name` 均必填):

- 旧: `{"code": "...", "project_name": "X"}` — 等价 `files=[{name: "Plugin.cs", code}]`
- 新: `{"files": [{"name": "Plugin.cs", "code": "..."}, {"name": "Helper.cs", "code": "..."}], "project_name": "X"}`

校验(全部在 `backend.compile` 之前,非法请求不触达后端):

- 文件名校验 `_FILE_NAME_RE = ^[A-Za-z0-9_][A-Za-z0-9_.-]*\.cs$`:
  仅叶子名(无目录分隔符 → 防路径穿越写 tmp 之外)、首字符字母数字下划线(防 `-` 开头被 msbuild 当开关)、
  仅 `.cs` 扩展(防覆盖 Plugin.csproj / csproj 注入)。
- 重复文件名 → 400;`files` 与 `code` 皆空 → 400;非法 project_name → 400(既有)。

## 2. 变更文件

| 文件 | 变更 |
|---|---|
| `compile_service/models.py` | 新增 `CompileFile(name, code)` dataclass + `resolved_files(req)`(files 显式则用之,否则退回单文件 Plugin.cs) |
| `compile_service/server.py` | `CompileRequest.files: list[CompileFile] \| None`(code 变可选)、`_FILE_NAME_RE`、名称校验+去重+空态 400;调 `backend.compile(files=..., project_name=...)` |
| `compile_service/backends/protocol.py` | 协议签名 `compile(files: list[CompileFile], project_name: str)` |
| `compile_service/backends/msbuild.py` | `compile(files, ...)`:每文件写入 tmp(名称纵深防御,直调后端也不能逃逸 tmp);csproj `<Compile Include="X.cs" />` 每文件一条(单文件 = 原行为,`AssemblyName=Plugin` 产物名不变);Include 属性 XML 转义 |
| `compile_service/backends/mock.py` | `compile(files, ...)`:规则对**全部文件源码拼接**命中(跨文件命中;错误 `file` 字段仍来自规则) |
| `agents/kingdee_plugin_agent/tools/compile_client.py` | 新增 `compile_files(files: list[tuple[str,str]], project_name)`;`compile(code, project_name)` 保留并委托之(单文件路径 w5 无感) |
| `tests/eval/run_eval.py` | `_compile()` 双契约分发(CompileClient.compile_files / 后端 compile(files)),评估级 MockCompiler 直调适配新协议 |
| `tests/test_compile_service.py` | 既有用例全部原样保留(3 个 stub 后端签名机械同步新协议);新增 11 个多文件用例 |

未变更:`w5_compile.py`(仍走 `client.compile(code, ...)` 单文件路径)、图结构、响应 JSON 形态
(仍 success/errors/dll_path/raw_output)。

## 3. 测试

全量:`267 passed, 0 failed`(基线 255,新增 12 个,~7min)。

新增用例:
- `resolved_files` 两态(files 缺省 → 单 Plugin.cs;显式 files → 原样)
- mock 多文件:坏代码放第二个文件 → 规则跨文件命中,file 字段来自规则;双文件干净 → 通过
- 名称校验 5 种非法名(`../evil.cs` / `sub/Plugin.cs` / `Plugin.csproj` / `plugin` / `-x.cs`)→ 400 且后端不被调用
- 重复名 → 400;files/code 皆空 → 400
- server 往返:files 载荷 → 200,错误来自第二个文件内容
- client `compile_files` → server(files 载荷)→ CompileResult 解析;干净多文件 → 通过
- msbuild(fake proc):双文件写入 tmp + csproj 两条 Compile Include;单文件仍一条
- msbuild 直调 `../evil.cs` / 空列表 → ValueError(纵深防御)

## 4. Windows 同步 + 真实 E2E

### 同步(需要 Windows 凭据,本环境不可达)

本环境(10.33.x 段 Linux)无法连接 Windows 编译机 10.33.17.130:SSH 22 / SMB 445 / WinRM 5985
均开放但需要密码(无可用凭据,未找到任何存储的凭据;不做口令猜测)。**同步步骤需人工执行**:

```bat
:: 在 Windows 编译机(E:\agentstore\compile_service\)执行,或本机 scp 后远程执行:
scp compile_service/backends/msbuild.py compile_service/server.py compile_service/models.py \
    <win-user>@10.33.17.130:E:/agentstore/compile_service/    :: 注:models.py 在 backends 上一级
scp compile_service/backends/protocol.py compile_service/backends/mock.py \
    <win-user>@10.33.17.130:E:/agentstore/compile_service/backends/
:: 或更稳:git pull(仓库已提交本功能)+ 重启计划任务
schtasks /end /tn "kingdee-compile-service"
schtasks /run /tn "kingdee-compile-service"
```

重启后验证:服务端 openapi 的 CompileRequest schema 出现 `files` 字段即新代码生效。

### E2E 结果(同步前基线,2026-08-09)

对 10.33.17.130:8100 实测(旧代码部署中):

| 用例 | 结果 |
|---|---|
| `GET /health` | `{"status":"ok"}` |
| 单文件(旧 API,`{"code":"public class P {}"}`) | `success:true`(真实 msbuild 编译通过,raw_output 含 Microsoft.Scripting 冲突告警——既有已知噪音) |
| 多文件(新 API,`files` 载荷) | **422** `Field required: body.code` —— 旧部署无 `files` 字段,符合预期(证明功能尚未上线) |

**同步后**运行 `docs/superpowers/plans/e2e_multifile.sh`(已备好,含 5 步):
1. 多文件 PASS:Plugin.cs 调用 Helper.cs(跨文件真实接线)→ 期望 success:true + dll_path
2. 多文件 FAIL:错误在第二个文件 → 期望 success:false 且错误行指向 Helper.cs(证明 csproj 接了第二文件)
3. GET /dll 拉取步骤 1 产物 → 200 + PE 头
4. `../evil.cs` → 400

### 真实团队插件 E2E(未完成,文件不可得)

`GlrAutoCreateSalesDeliveryOrder.cs` + `GlrKingdeeHelper.cs`(List_AutoSales 依赖)在本机与仓库均不存在
(团队 6 个真实插件 .cs 已灌入本地 Chroma 知识库 data/kingdee-rag,gitignored,原始文件不在本机);
`KingdeePackage` 目录本地不存在。**需要在 Windows 机或团队文件共享上取到 GlrKingdeeHelper.cs 后**,
再以 `files=[{GlrAutoCreateSalesDeliveryOrder.cs}, {GlrKingdeeHelper.cs}]` 跑一次真实插件多文件编译
作为最终 E2E(预期 success:true 产 DLL——该插件此前单文件编译因缺 GlrKingdeeHelper 失败)。

## 5. 关注点

1. **Windows 同步 + 真实 E2E 未执行**(凭据不可得):本地测试与旧服务基线已验证;新代码上线 + 上表 E2E
   需人工按 §4 执行。
2. **w5 尚未使用多文件**:本功能是编译服务/客户端能力;w5 单文件路径不变。w3 生成多文件项目
   (主类 + helper 分文件)的接线是后续工作(设计稿范围外)。
3. **mock 后端的 file 字段**:规则命中时报 `rule.file`(Plugin.cs)而非实际命中文件——mock 仅做
   CI/开发门,不追精确位置;真实 msbuild 输出由解析器逐行提取真实文件(Helper.cs 错误会正确归属)。
4. **eval run_eval 契约分发**(`_compile`):按 `compile_files` 属性存在性区分 CompileClient/后端;
   后续若再加编译入口需保持此分发或改注入统一对象。
5. 名称白名单禁止中文/空格文件名(金蝶项目惯例为英文名,符合约束;如未来需要可放宽)。
