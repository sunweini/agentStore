# Windows 部署全配置化报告(零硬编码路径)

- 日期:2026-08-09
- Commit:`23c3bc1` feat(compile): Windows 部署全配置化(env 覆盖 + 代码相对默认,零硬编码路径)
- 目标:compile_service 全部部署路径改为**环境变量可覆盖 + 代码相对默认**,消除硬编码 Windows/容器路径

## 逐项变更

### 1. compile_service/backends/msbuild.py

- `_FRAMEWORK_MSBUILD`(C:\Windows\...\MSBuild.exe)保留为**最后兜底**,新增 `FRAMEWORK_MSBUILD_PATH` env 覆盖。
- `default_msbuild_path()` 探测优先级(从高到低):
  1. `MSBUILD_PATH` env(后端直接读 env,独立于 server.py 参数,standalone 可用)
  2. PATH 的 msbuild(VS 环境自动探测)
  3. `FRAMEWORK_MSBUILD_PATH` env(存在性校验同兜底路径)
  4. 硬编码 Framework 路径(最后兜底,`exists()` 才返回)
  5. 退化 `"msbuild"` 交由 subprocess 报错
- `artifact_dir` 缺省:`Path("data/kingdee-compiled")`(cwd 相对)→
  `_DEFAULT_ARTIFACT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "kingdee-compiled"`
  (msbuild.py 上溯 3 层 = 仓库根/data/kingdee-compiled);签名改 `Path | None = None`,
  构造函数内 `None → _DEFAULT_ARTIFACT_DIR`,仍可经构造函数覆盖。

### 2. compile_service/server.py

- `REFS_DIR` 缺省:`"/app/references"`(容器路径)→ `_DEFAULT_REFS_DIR =
  Path(__file__).resolve().parent / "build" / "references"`(compile_service/build/references,
  Windows 原生部署可用,容器内代码挂 /app 时同样命中);`os.getenv("REFS_DIR") or _DEFAULT_REFS_DIR`
  (空串也回落默认)。
- 新增 `COMPILE_ARTIFACT_DIR` env → 透传 `MsbuildCompiler(artifact_dir=...)`(不设则走后端代码相对默认)。
- `_backend_from_env` docstring 更新为完整 env 清单。

### 3. compile_service/Dockerfile(对齐项)

- 新增 `ENV REFS_DIR=/app/references`(含注释):镜像内 references 仍固定 /app/references
  (COPY 目标),显式声明避开代码相对默认值在容器内接管 —— 容器部署行为完全不变,
  服务端代码保持零硬编码路径。

### 4. 端口/监听(PORT/HOST)

- 无代码变更:uvicorn `--host/--port` 由启动命令控制,服务不读 PORT/HOST env。
- windows-deployment.md §6 新增说明:需要 env 驱动就在 bat 里 `set PORT=8088` 后 `--port %PORT%`。

### 5. 文档

- **windows-deployment.md**:start_compile.bat 全 `%~dp0` 相对(REFS_DIR/TARGET_FRAMEWORK/
  MSBUILD_PATH/FRAMEWORK_MSBUILD_PATH/COMPILE_ARTIFACT_DIR,日志 `%~dp0..\uv.log`);
  env 表补 FRAMEWORK_MSBUILD_PATH/COMPILE_ARTIFACT_DIR 行;§5 增加 fetch_kingdee_dlls.ps1
  方式 + KINGDEE_BIN_DIR;§10.2 更新探测顺序;全文 E:\uv.log → 仓库根\uv.log(%~dp0 相对)。
- **manual.md**:§1.4 bat 示例同步 %~dp0 + 新 env;Q 日志路径与 KINGDEE_BIN_DIR 提点;env 可选列表补全。
- **agents/kingdee_plugin_agent/CLAUDE.md 接真实环境**:补 FRAMEWORK_MSBUILD_PATH /
  COMPILE_ARTIFACT_DIR / KINGDEE_BIN_DIR,声明"代码相对默认 + env 可覆盖,零硬编码部署路径"。
- **.env.example**:编译服务 env 组注释全量(REFS_DIR/TARGET_FRAMEWORK/MSBUILD_PATH/
  FRAMEWORK_MSBUILD_PATH/COMPILE_ARTIFACT_DIR/KINGDEE_BIN_DIR + PORT/HOST 约定)。
- **CHANGELOG.md**:v1.16.0 条目。

### 6. compile_service/fetch_kingdee_dlls.ps1

- 新增 `KINGDEE_BIN_DIR` env:设置时作为源目录(-SourceDir 参数优先于 env,env 先于自动探测);
  头部 Usage 注释补充;候选路径自动探测列表保留。

## 测试

- 新增 6 个单测(tests/test_compile_service.py):
  - `test_default_msbuild_path_msbuild_env_respected`(MSBUILD_PATH env 压过 PATH 探测)
  - `test_default_msbuild_path_framework_env_override`(FRAMEWORK_MSBUILD_PATH env)
  - `test_default_msbuild_path_framework_fallback_intact`(无 env 时硬编码兜底 intact)
  - `test_artifact_dir_default_code_relative`(无参构造 → 仓库根/data/kingdee-compiled)
  - `test_backend_from_env_default_refs_dir_code_relative`(REFS_DIR 缺省命中
    compile_service/build/references,且 artifact_dir 走代码相对默认)
  - `test_backend_from_env_artifact_dir_env`(COMPILE_ARTIFACT_DIR 透传)
- 全量回归:`pytest tests/ -q` → **255 passed**(原 249 + 新增 6),0 failed。

## 文件变更

- compile_service/backends/msbuild.py
- compile_service/server.py
- compile_service/Dockerfile
- compile_service/fetch_kingdee_dlls.ps1
- tests/test_compile_service.py
- docs/kingdee-plugin-agent/windows-deployment.md
- docs/kingdee-plugin-agent/manual.md
- agents/kingdee_plugin_agent/CLAUDE.md
- .env.example
- CHANGELOG.md

## 关注点

1. **容器路径一致性**:REFS_DIR 代码相对默认在容器内是 /app/build/references,而镜像布局
   references 在 /app/references —— 已用 Dockerfile `ENV REFS_DIR=/app/references` 显式兜住,
   容器行为不变;若将来改镜像布局为 build/references,删掉该 ENV 即可。
2. **`FRAMEWORK_MSBUILD_PATH` 语义**:覆盖"Framework 兜底"位置(位于 PATH 探测之后),不是
   全局最高优先级;要全局钉死 msbuild 仍用 `MSBUILD_PATH`。文档已按此写明。
3. **测试写入仓库目录**:`test_backend_from_env_default_refs_dir_code_relative` 会临时在
   compile_service/build/references 写一个 .dll 并在 finally 删除(该目录仅 .gitkeep,data/ 已
   gitignore);若断言失败留痕也仅一个空壳文件,不污染构建。
4. **compile_client.py(agent 侧)仍有 cwd 相对默认** `artifact_dir=Path("data/kingdee-compiled")`:
   属 agent 运行侧(非 Windows 部署侧),本次按任务范围未改;如需同样代码相对化可后续跟进。
