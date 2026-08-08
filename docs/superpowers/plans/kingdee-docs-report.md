# kingdee-plugin-agent 三份文档交付报告

日期:2026-08-08
提交:docs: kingdee-plugin-agent 项目文档/技术文档/使用手册

## 交付文件

| 文件 | 行数 | 说明 |
|---|---|---|
| `docs/kingdee-plugin-agent/project.md` | 84 | 项目文档(决策者/新人):背景目标、成功标准、范围、架构概览、里程碑、后续规划、技术栈 |
| `docs/kingdee-plugin-agent/tech.md` | 358 | 技术文档(开发者):图结构、任务契约、8 worker、skill 体系、知识库、错误处理、安全、部署、测试、性能预算、债务 |
| `docs/kingdee-plugin-agent/manual.md` | 191 | 使用手册(使用者):快速开始、CLI、Web、API 端点表、FAQ、交付物解读 |

(行数为 `wc -l` 实测;字数合计约 1.5 万字符,其中 tech.md 最深。)

## 准确性核验方法

三份文档均**先读代码后落笔**,核验过的关键文件:

- `agents/kingdee_plugin_agent/CLAUDE.md`(架构/约束/债务基准,文档引用并在头部声明"以 CLAUDE.md 为唯一事实来源")
- `agent.py`(图拓扑/`_send_payload`/`default_recursion_limit`/`_advance_status`)、`graph/supervisor.py`(决策顺序/`STATUS_TO_WORKER`/LLM 校验)、`graph/state.py`(字段/reducer/常量)
- 8 个 worker 源文件(契约、LLM 调用、降级分支逐条对照)
- `api.py`(5 端点、apikey 优先级、SSE 语义、验收沉淀)、`cli.py`(门控/退出码/交互格式)
- `skills/loader.py`(6 skill 摘要、structured_with_skill 绑定形态、2 回合上限)
- `common/rag.py`(四库、BM25+RRF 融合公式 c=60、ExperienceStore 签名去重、seed 7 条)
- `compile_service/server.py` + backends + error_parser + Dockerfile + docker-compose + docker-entrypoint
- `tools/`(compile/smoke/package/kingdee_api)、`store/artifact_store.py`(路径白名单)、`seed/seed_load.py`、`templates/__init__.py`(逐 token 渲染)
- `web/kingdee-demo.html`(澄清流/任务矩阵/验收)、`tests/eval/`(EVAL_MOCK_RULES/trigger 断言)、CHANGELOG v1.8.0(164 项测试数)

约束数值核对:返工预算 3 / 并发 3 / 编译轮次 5 / 澄清 10 轮 / recursion 100+20n(CLI/API 按 n=10 → 300)——与 CLAUDE.md 一致。

## 未验证项(文档中均已显式标注)

- 线上 DeepSeek 验证 load_skill 绑定(tech §4.2、project §5.2)
- 真实金蝶环境 WebAPI 端点/响应结构 = 初始契约占位(tech §3 w5.5 / §11、manual §7)
- E2E 启动门:团队金蝶 BOS DLL → 真实容器编译 3 类型样例(project §5.2、tech §9)
- Linux 容器 BOS 编译兼容性(tech §8.2)

## 关注点

1. **测试未在本环境复跑**:本环境未安装 langchain 依赖,pytest 收集失败(ImportError),"164 项测试全过"引用自 CHANGELOG v1.8.0 记录,未现场验证。
2. **CHANGELOG 更新**:按项目规则(dev-standards §4)"每次开发收尾必须更新 CHANGELOG.md",本次 docs 交付追加 v1.8.1 文档条目,与三份文档同一提交。
3. **范围裁剪**:task 要求的"16+ 错误场景表"实收 25 条;"检索路由表"以代码实际接线为准(w2/w3/w4/w5/w7 六行);设计文档 §8 中的"时间预算 15/30min、需求版本冻结、任务进行中改需求"等项在代码中无实现,未写入(避免文档与代码脱节,以 CLAUDE.md 债务标注为准)。

---

## 勘误(评审修复,提交:docs: 三份文档勘误(容器启动语义/交付物内容/bm25 接线声明))

文档评审发现 1 Critical + 2 Important + 6 Minor,全部修复(均已对照代码核实):

### Critical

- **manual.md §1.3 容器启动语义错误**:原文"未提供 DLL 时容器会以 mock 后端启动"不成立 —— Dockerfile **无条件**设 `COMPILE_SERVICE_REQUIRES_DLLS=1`,references 为空时 `create_factory` 构造 MsbuildCompiler 抛 CompileUnavailableError,容器启动即失败(报"DLL 未到位")。已改为:无 DLL 时 `docker-compose up -d` 启动失败;mock 后端仅在本机不带该环境变量直接运行 `create_factory`(开发/测试)时生效。

### Important

- **manual.md §6/Q7 交付物内容失实**:w6_package.py 恒传 `dll_path:""`(任何后端下 DLL 都不入包),且从不提供 design/review → records/*.json 恒为 `{}`。已改为如实描述:records 为空占位(未接线)、DLL 待真实编译后端产出后入包;tech.md §3 w6 产物行同步修正。
- **tech.md §5.1 api_ref 行 bm25_weight=0.7 声称已接线**:w2/w3 调用 `hybrid_search` 未传 bm25_weight(默认 0.5),0.7 仅是知识库路由表约定。已改为"默认 bm25_weight=0.5;0.7 为约定,未接线"。

### Minor

- tech.md §4.3 prompt 行数范围:实际 4~18 行(w1 仅 4 行),已修正措辞。
- tech.md §3 w5/w5.5 + 错误表 row5:"退回 w3/w4 或问用户"/"退回 w5/w3" → 均改"退回 w3 重新生成"(needs_rework 恒映射 w3,代码 STATUS_TO_WORKER 核实)。
- tech.md §8.1:compose 文件在 Plan C 落地后未更新,api 服务仍注释未启用(原文"待 Plan C 落地后启用"过时)。
- manual.md §3.2 任务矩阵阶段条:演示页 PHASES 以"交付"结尾(非"沉淀"),已改。
- manual.md Q2:删除不可达分支"编译客户端未配置(COMPILE_SERVICE_URL 缺失)"(compile_client_from_env 恒返回默认 localhost:8000 客户端)。
- tech.md §4.2 摘要层精度:`skill_summary()` 仅注入 w1 generate_questions,其余 worker 只有 SKILL_HINT,supervisor 无注入 —— 已按实际接线修正。
