# kingdee-plugin-agent 使用手册

> 面向使用者:如何配置环境、用 CLI / Web 发起插件开发任务、解读交付物、排查常见问题。
> 技术细节见 [tech.md](tech.md),项目背景见 [project.md](project.md)。

## 1. 快速开始

### 1.1 环境准备

复制 `.env.example` 为 `.env` 并配置(密钥只放 `.env`,不提交):

```bash
cp .env.example .env
```

必须配置 4 组:

```ini
# 1) LLM(DeepSeek,OpenAI 兼容)
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com

# 2) 金蝶云星空环境(KD_* 4 项,缺任一则 Web API 拒绝建任务 503;CLI 至少需 KD_BASE_URL)
KD_BASE_URL=https://your-kingdee-host
KD_USERNAME=your-account
KD_PASSWORD=your-password
KD_DATA_CENTER=your-data-center

# 3) 编译服务地址(缺省 http://localhost:8000)
COMPILE_SERVICE_URL=http://localhost:8000

# 4) Web API 鉴权 apikey(建任务/查状态/回答/验收都要带 X-API-Key 头)
KINGDEE_API_KEY=sk-demo-key
```

可选:`COMPILE_SERVICE_REQUIRES_DLLS=1` + `REFS_DIR` + `TARGET_FRAMEWORK=v4.8`(真实 msbuild 后端用 —— **Windows 部署必配**,见 1.3 与 [windows-deployment.md](windows-deployment.md))。

### 1.2 灌入经验库种子(首次必做)

```bash
python -m agents.kingdee_plugin_agent.seed.seed_load
# 输出:种子灌入完成:新增 10 条(幂等:重复执行新增 0 条)
# 可选 --data-dir <dir> 指定数据目录(默认 data/kingdee-rag,与 RAG 客户端一致)
```

种子提供编译错误的基础经验,后续任务的踩坑会由 w7 持续沉淀(proposed 态,人工核验后转 verified)。

### 1.3 灌入 RAG 知识库(guide/api_ref,首次必做)

```bash
# 内部 skill 文档(SKILL.md + references)→ guide
python -m agents.kingdee_plugin_agent.tools.ingest --seed-internal --collection guide
# 官方文档单页 → guide / api_ref(标题缺省从页面 <title>/<h1> 提取)
python -m agents.kingdee_plugin_agent.tools.ingest --url <URL> --collection guide|api_ref
# 目录批量导入 *.md(递归,自动去 YAML frontmatter)
python -m agents.kingdee_plugin_agent.tools.ingest --dir <目录> --collection guide
# 可选 --data-dir <dir>(默认 data/kingdee-rag)
# 幂等是"去重式":内容未变重跑新增 0;编辑已灌入文档后须先删旧再重灌:
# python -m agents.kingdee_plugin_agent.tools.ingest --delete-source <source> --collection guide|api_ref
```

当前已灌入内容(2026-08-09 终态,重跑新增 0):

- **guide(72 chunks / 27 源)**:内部 skill 7 份 SKILL.md + 14 份 references
  (design-builder / code-generator / code-reviewer / compile-fixer /
  knowledge-steward / requirement-clarify,53 chunks);金蝶官方 6 页
  (BOS 平台知识地图、星空 BOS 平台简介、熊说金蝶 BOS 知识库、BOS FAQ 精选、
  收款单扩展实操、AI 辅助二开,19 chunks);
- **api_ref(4 chunks / 3 源)**:金蝶官方 3 页(星空企业版开发笔记、WebAPI 多选
  基础资料、WebAPI 系统集成主题)。

> 模板类文件(`templates/*.cs`)不入库 —— 它们是代码模板,w3 直接使用,无需检索。
> 灌入后可用 `RagClient().hybrid_search("guide", "插件开发")` 抽查命中(应含
> 内部 skill 文档);`hybrid_search("api_ref", "BusinessDataServiceHelper")`
> 应命中星空企业版开发笔记。
>
> **活页漂移说明**:BOS FAQ 精选页(685345938776315392)是人工持续策展的活页,
> 正文会随编辑在源侧变化 —— 重跑偶发 +1~3 属源侧内容更新(非管线缺陷),
> 刷新该页用 `--delete-source` + 重灌。其余 8 个官方页与内部文档重跑稳定 +0。

### 1.4 起编译服务(需要时)

编译环节(w5)依赖编译服务;服务不可用时该环节报 BLOCKED(不计编译轮次)并最终标记失败。真实 msbuild 后端需要金蝶 BOS DLL 放入 `compile_service/build/references/`。

**推荐部署方式:Windows 原生部署(E2E 门已验证)** —— 在金蝶服务器本机或同网段 Windows 机器上直接跑编译服务,完整步骤见 [windows-deployment.md](windows-deployment.md)(.NET 4.8 Developer Pack + 金蝶 WebSite\bin DLL + start_compile.bat + schtasks 保活):

```bat
rem start_compile.bat(内容,详见 windows-deployment.md)
set COMPILE_SERVICE_REQUIRES_DLLS=1
set REFS_DIR=E:\kingdee\compile_service\build\references
set TARGET_FRAMEWORK=v4.8
cd /d E:\kingdee\compile_service
uvicorn compile_service.server:create_factory --host 0.0.0.0 --port 8000 >> E:\uv.log 2>&1
```

```bash
curl http://<windows-机>:8000/health   # {"status":"ok"}
```

Docker 方式(Linux 容器,备选,未验证):`docker-compose up -d`(8000 端口;镜像 Dockerfile **无条件**设置 `COMPILE_SERVICE_REQUIRES_DLLS=1` 固定走真实后端分支,references 目录为空时容器**启动即失败** —— 未提供金蝶 BOS DLL 前不要依赖 docker-compose 起编译服务;Linux 容器内 BOS 编译兼容性未验证,Windows 原生部署为实际采用方案)。mock 后端仅在**本机不带该环境变量**直接运行 `uvicorn compile_service.server:create_factory`(开发/测试)时生效,不当质量门;测试环境(w5)在无编译服务时按 BLOCKED → 标记失败处理。

### 1.5 校验环境

```bash
python -m agents.kingdee_plugin_agent.cli "测试需求" --env test   # 未配 KD_BASE_URL 会提示错误并退出 1
```

## 2. CLI 用法

```bash
python -m agents.kingdee_plugin_agent.cli "<需求描述>" --env <环境名>
```

示例:

```bash
python -m agents.kingdee_plugin_agent.cli "给采购单审核加库存校验" --env test
```

### 2.1 交互澄清

agent 一次一问(≤10 轮),逐条回答,最后给出确认摘要:

```
[澄清 1] 该插件挂在哪个单据上(FormId)?触发时机是什么(提交/审核/保存)?
> 采购订单(PUR_PurchaseOrder),提交时校验

[澄清 2] 库存不足时的处理方式?
> 禁止提交并提示

## 需求确认摘要
### 已确认决策
- 该插件挂在哪个单据上(FormId)?触发时机是什么(提交/审核/保存)?: 采购订单(PUR_PurchaseOrder),提交时校验
- ...
### 假设(你没说的,我按此处理,不认可请指出)
- 未说明的细节按金蝶 BOS 默认规范处理
> 确认

── TodoList 摘要 ──
  A1 [bill] delivered  采购订单审核库存校验(bill)
  交付包: data/kingdee-deliverables/deliverable-A1-20260808-103000.zip
全部子任务交付完成
```

- 不认可确认摘要时,输入补充意见,agent 会调整后再次确认;最多再确认 1 次,仍不确认会带假设强制收口。
- 中途缺信息时 agent 也会以 `[询问]` 形式挂起问用户。
- **退出码**:全部子任务交付 → `0`;失败/中止/环境门槛 → `1`。

### 2.2 输出解读

- **TodoList 摘要**:每个子任务一行 `id [类型] 状态 标题`;状态见 tech.md §2.3(注意 `needs_rework` 表示退回重做,`failed` 为终态)。
- **交付包路径**:`data/kingdee-deliverables/deliverable-<子任务id>-<时间戳>.zip`,多子任务各得一个包(v1 逐包交付)。

## 3. Web 用法

### 3.1 起 API

```bash
uvicorn "agents.kingdee_plugin_agent.api:create_app" --factory --reload
# 默认 http://localhost:8000
```

### 3.2 演示页

浏览器打开 `web/kingdee-demo.html`(静态文件,直接双击或起静态服务均可):

1. **输入区**:填需求描述(如"给采购订单审核增加库存校验…")、选目标环境(test/prod)、填 API Key(默认 `sk-demo-key`,须与 `KINGDEE_API_KEY` 一致)。
2. **澄清对话流**:AI 一次一问,输入框逐条回答;最后出现确认摘要面板,可"确认通过,开始开发"或输入补充意见修正。
3. **任务矩阵(TodoList)**:每个子任务一张卡片,实时显示状态徽章与阶段进度条(设计→生成→审查→编译→冒烟→打包→交付,与演示页 PHASES 一致)。
4. **验收操作**:任务完成后对交付物做 **accept / reject**;拒绝时必须填原因 —— 拒绝原因会写入经验库(proposed 态),让后续任务避开同样的符合性问题。
5. **SSE 实时流**:进度事件实时推送;断线自动重连(重放已发事件,seq 去重),兜底可用 `GET /tasks/{id}/state` 拉全量快照。

## 4. API 端点表

所有端点需 `X-API-Key` 头;apikey 优先级:`create_app(api_key=...)` > 环境 `KINGDEE_API_KEY` > `API_KEYS_JSON` 首个 key,未配置默认 401。

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/tasks` | `{"requirement": "…", "env": "test"}` | `{"task_id": "…", "status": "created"}`(200);400 requirement 必填;503 KD_* 4 项缺失(点明缺项);401 apikey 无效 |
| GET | `/tasks/{id}/events` | — | SSE 流,事件:`todo`(子任务快照)/ `interrupt`(澄清问题/确认摘要)/ `acceptance`(验收结论)/ `done`(含全量快照)/ `error`;断线重连自动重放,结束发 sentinel 关流 |
| GET | `/tasks/{id}/state` | — | 全量快照:`{task_id, status(running/waiting/done/error), done, error, interrupt, todo, final_deliverables, acceptance}`;404 任务不存在 |
| POST | `/tasks/{id}/answers` | `{"answer": "…"}`(或 `text`) | `{"ok": true, "task_id": "…"}`;400 answer 必填;409 任务未在等待输入(30s 等待超时/已结束);**需求确认冻结后仅接受执行中问题(ask_user)的恢复,其余 409** |
| POST | `/tasks/{id}/acceptance` | `{"accepted": true\|false, "reason": "…"}` | `{"ok": true, "task_id": "…", "acceptance": {accepted, reason, at}}`;拒绝 + 原因 → 经验库 propose("ARTIFACT", sha256(reason)[:12], …) |
| POST | `/tasks/{id}/feedback` | `{"reason": "…"}` | `{"ok": true, "task_id": "…", "feedback": {reason, at}}`;部署后行为错误手动上报 → 经验库 propose("DEPLOY", sha256(reason)[:12], reason, …)(proposed 态,失败不阻塞);404 任务不存在;401 apikey 无效 |

`POST /tasks` 建任务后由后台线程执行图;`interrupt` 事件挂起时,客户端把用户答复 POST 到 `/answers` 恢复图(`Command(resume=...)`),与 CLI stdin 同语义。

## 5. 常见问题

**Q1: 报"错误:未配置金蝶环境(KD_BASE_URL)"(CLI)或 503 缺 KD_* 项(API)**
→ 环境硬门槛:不配金蝶环境不进图。补齐 `.env` 的 `KD_BASE_URL/KD_USERNAME/KD_PASSWORD/KD_DATA_CENTER` 4 项后重试。

**Q2: 任务到编译环节失败,报"编译服务不可用"**
→ 编译容器未起:先 `docker-compose up -d` 并 `curl http://localhost:8000/health` 确认 ok(注意:无金蝶 BOS DLL 时容器启动即失败,见 1.3)。服务故障报 BLOCKED,**不计编译轮次**;但当前任务会标记 failed,修复服务后需重新建任务。真实 msbuild 后端需金蝶 BOS DLL 到位(见 1.3)。

**Q3: 如何清空经验库重灌种子?**
```bash
rm -rf data/kingdee-rag
python -m agents.kingdee_plugin_agent.seed.seed_load
```
种子灌入幂等,重灌安全;但会同时清掉 w7 沉淀的 proposed 条目(未人工核验前请先导出)。

**Q4: 任务中断/API 重启后任务不见了?**
→ v1 任务存在进程内内存(`app.state.tasks`),重启即丢、无恢复;CLI 被中断(非交互终端 EOF)会提示"任务已中止"退出 1。重跑任务即可;持久化存储列入后续规划。

**Q5: 澄清确认后 agent 还是按我的补充意见收口了?**
→ 确认摘要最多再确认 1 次,仍不确认会带假设强制收口(防无限循环);不认可的假设可在确认环节提出,未提出的细节按"金蝶 BOS 默认规范"处理。

**Q6: 返回 401 apikey 无效?**
→ `X-API-Key` 头值须与 `KINGDEE_API_KEY`(或 `API_KEYS_JSON` 首个 key)一致;未配置任何 key 时默认拒绝全部请求。

**Q7: 交付包能直接用吗?**
→ 交付包含源码 + 部署说明 + records(design.json/review.json 由 w6 从产物库读入,缺失容错为空;`spec.json` 记录需求版本号与冻结的需求快照);DLL 在真实编译后端(mock 后端无产出)时入包 `bin/Plugin.dll`。**上线前仍需人工 review 并在真实金蝶环境验收** —— 编译侧 E2E 门已达成(三类型样例真实编译通过,见 windows-deployment.md),但 WebAPI 客户端端点与冒烟验证路径仍为初始契约占位(未在真实实例验证);编译后端无 DLL 产出时冒烟环节会显式跳过(标注「无 DLL」),不会拿源码冒充产物。

**Q8: 需求确认后想改需求怎么办?**
→ 需求在确认时冻结(`spec_version=1`),确认后任何输入都不会再改动需求规格 —— 中途问题(ask_user)的回答只作为反馈记录;确认后 answers 端点只接受执行中问题的恢复(其余 409"需求已确认并冻结")。要改需求请**开一个新任务**重跑(设计 §8 需求版本冻结)。

**Q9: 任务跑了很久没结束?**
→ 全流程时间预算 30min 图级总闸:超时且仍有未交付子任务 → 自动收尾失败(`fail:时间预算耗尽`),剩余子任务标记 failed。单轮编译超时(客户端 httpx 120s / msbuild 后端 180s)、单任务编译阶段 ≤15min(5 轮上限,天然满足)由编译环节内部覆盖。真超时通常是编译服务不可用/LLM 故障,先查 §5 Q2。

**Q10: 编译服务端口被金蝶占用了?**
→ 金蝶 WebAPI 常占 8000 端口。换端口:`start_compile.bat` 里 `--port` 改别的端口(如 8088,先确认防火墙放行),agent 侧 `.env` 的 `COMPILE_SERVICE_URL` 同步改为 `http://<windows-机>:8088`。

**Q11: 编译返回 500/服务起不来?**
→ Windows 部署下服务日志写在 `E:\uv.log`(start_compile.bat 重定向),先看日志尾部找 traceback;常见三类:① 缺 .NET 4.8 Developer Pack(msbuild 报找不到参考程序集)→ 装 DevPack;② references 目录缺 DLL(启动即报"DLL 未到位")→ 从金蝶服务器 WebSite\bin 拷贝;③ 端口被占 → 换端口(见 Q10)。

**Q12: 编译报 warning MSB3274/MSB3275(引用被跳过)?**
→ 金蝶 BOS DLL 是 .NET 4.8,编译目标框架低于引用程序集时引用被**静默跳过**(不报 error,编出来缺类型)。把 `TARGET_FRAMEWORK` 提到 `v4.8`(start_compile.bat 已默认设置;改了要重启服务)。经验库有对应种子(MSB3274/MSB3275)。

## 6. 交付物解读(zip 内容)

```
deliverable-<子任务id>-<时间戳>.zip
├── source/Plugin.cs        # 插件源码(w3 生成 + w5 修复后的最终版)
├── bin/Plugin.dll          # 编译产物(真实编译后端产出时入包;mock 后端无产出 → 无此条目)
├── deploy.md               # 部署说明(上传 DLL 到金蝶 BOS 插件目录,刷新注册)
└── records/
    ├── spec.json           # 需求版本冻结记录:{"spec_version": 1, "requirement_spec": 冻结的需求快照}
    ├── design.json         # 设计文档(w6 从产物库读入,缺失容错为空)
    └── review.json         # 审查记录(含 Minor 全部意见,缺失容错为空)
```

部署步骤:把 `bin/Plugin.dll` 上传到金蝶 BOS 插件目录 → 刷新插件注册 → 按部署说明绑定单据 → 冒烟验证。

## 7. 限制与未验证项

- **插件类型**:bill(单据)/ service(服务)/ list(列表),暂不含定时任务。
- **E2E 门(编译侧)已达成**:三类型样例在 Windows Server 2016 金蝶服务器(WebSite\bin 真实 DLL + .NET 4.8 DevPack + Framework MSBuild)真实编译通过并产出 DLL,见 [windows-deployment.md](windows-deployment.md)。
- **未线上验证**:load_skill 工具绑定未对真实 DeepSeek 验证;真实金蝶环境 WebAPI 端点/响应结构为占位契约(编译侧已真实验证,元数据/冒烟侧仍未);Linux 容器内 BOS 编译兼容性未验证(实际采用 Windows 原生部署)。
- **v1 单环境**:`--env` 记录进 `state.environment["env_name"]`(CLI/API 初始 state,节点可感知),未做环境级差异化配置。
