# Kingdee-Plugin-Agent 设计:金蝶云星空插件开发 Agent

日期:2026-08-08
状态:已批准

## 1. 背景与目标

- 团队内部工具:输入自然语言需求 → 自动完成金蝶云星空(BOS)插件开发全流程:需求 → 设计 → 代码 → 编译验证 → 打包交付。
- 插件类型:单据/表单插件、服务插件、列表插件(不含定时任务)。
- 用户需求常不完整,需交互式澄清(参考 superpowers brainstorming 方法论改造),不让 agent 猜。
- 支持复合需求:一个需求涉及多插件类型,拆子任务、依赖拓扑编排、并行/串行派发。
- 每次开发收尾沉淀知识(踩坑/规范/API 用法),agent 越用越强。
- 基于 LangChain/LangGraph,开发依据 langchain MCP 文档/API(见 docs/dev-standards.md)。
- 成功标准:编译通过率高、代码符合需求、返工少。

## 2. 技术选型

| 项 | 选择 |
|---|---|
| 编排 | LangGraph Supervisor 图,1 主管 + 13 worker(子图) |
| LLM | DeepSeek(common/llm.py 工厂,多供应商) |
| 元数据 | 金蝶云星空 WebAPI 客户端(只读查询 FormId/字段/操作/服务定义) |
| 知识 | RAG 四库:API 参考库/开发指南库/规范库/经验库(详见 §6) |
| 向量库 | Chroma(本地文件,轻量) |
| 嵌入 | BGE 系中文模型(本地,DeepSeek 无 embedding API,避免云供应商绑定) |
| 编译 | 容器封装 HTTP 服务(镜像预置金蝶 BOS DLL,团队提供,注意授权) |
| 入口 | CLI + Web(参照 web/demo.html 模式) |
| 契约 | 任务下发/上报/审查裁决模板(参照 superpowers subagent-driven-development) |

## 3. 架构:1 主管 + 13 worker

```
用户输入(Web/CLI)
   │
   ▼
┌──────────────────────────────┐
│  SUPERVISOR 主管 agent         │ ← 编排、派发、跟踪 TodoList、升级
└──────────────────────────────┘
   │
   ├─▶ [w1] 需求分析 agent(通用,交互式)
   │     需求澄清(问题模板,元数据驱动提问,用户确认门槛)
   │     产出:需求规格 spec + 任务计划 plan(子任务+依赖)
   │
   ├─▶ [w2] 设计 agent ×3(按类型拆)
   │     ├─ w2a 单据/表单设计   ├─ w2b 服务设计   ├─ w2c 列表设计
   │
   ├─▶ [w3] 代码生成 agent ×3(按类型拆)
   │     ├─ w3a 单据/表单生成   ├─ w3b 服务生成   ├─ w3c 列表生成
   │
   ├─▶ [w4] 代码审查 agent ×3(按类型拆,独立审查)
   │     ├─ w4a 单据审查   ├─ w4b 服务审查   ├─ w4c 列表审查
   │     发现问题 → 退回对应 w3(上限 3 轮)
   │
   ├─▶ [w5] 编译修复 agent(通用)
   │     提交容器编译 → 错误 → 检索经验库修复 → 重编译(上限 5 次)
   │     失败退回 w3/w4
   │
   ├─▶ [w6] 打包 agent(通用)
   │     子任务产物合并 → 交付包(源码+DLL+部署说明+设计/审查记录)
   │
   └─▶ [w7] 知识沉淀 agent(通用)
        提炼:踩坑/编译错误模式 → 经验库;规范偏差 → 规范库;API 用法 → RAG
        失败不阻塞交付,记待沉淀队列
```

### 3.1 复合需求处理

- w1 拆解:多类型识别 → 子任务划分 → 依赖标注(例:单据插件 A 调用服务插件 B,依赖 B 接口)。
- 主管按依赖拓扑编排:无依赖子任务并行派发,有依赖先做被依赖者。
- 交叉校验:w4 审查时对齐子任务间接口契约(设计文档共享)。
- w6 打包时子任务产物合并成一个交付包。
- State 为"子任务池":每个子任务带自己的需求/设计/代码/编译/审查状态。

## 4. 数据流

```
① 输入:用户需求文本(CLI/Web)
② w1 澄清循环:提问→答→下一问(元数据辅助)→ spec 草稿 → 用户确认
   → 拆子任务 plan(依赖拓扑)→ 入 State
③ 主管按依赖派发 w2x 设计:RAG(API+指南,类型过滤)→ 设计文档
④ w3x 代码生成:设计 + RAG → C#
⑤ w4x 审查:规范库整库 + API 抽查 → 意见;Critical/Important 退回 w3x
⑥ w5 编译:提交容器 → 错误列表 → 检索经验库修复 → 重编(上限 5)
⑦ w6 打包:子任务产物合并 → 交付包
⑧ w7 沉淀:踩坑→经验库,规范偏差→规范库
⑨ 交付:CLI 目录 / Web 下载
```

## 5. 任务契约与状态跟踪(参照 superpowers subagent-driven-development)

### 5.1 任务下发模板(主管 → worker)

```
TASK_ID:      <子任务号>.<阶段号>         例: A2.design
TYPE:         设计|生成|审查|编译|打包|沉淀
PLUGIN_TYPE:  单据|服务|列表
INPUT:        依赖产物引用(需求文档/设计/代码 — State key)
RAG:          指定检索库 + 过滤条件
验收标准:     该环节可验证的完成标准
上限:         退回轮次 / 编译轮次
```

### 5.2 上报契约(worker → 主管)

```
STATUS:   DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
产物:     写入 State 的 key + 文件路径
证据:     编译日志 / 审查意见 / 测试摘要
关注点:   风险、疑点
原因:     BLOCKED/NEEDS_CONTEXT 必须说明(主管直接据此行动)
```

- BLOCKED:完不成;NEEDS_CONTEXT:缺信息。主管汇总问用户,worker 不许猜。
- DONE_WITH_CONCERNS:做完了但有疑虑,主管挂标记展示给用户。

### 5.3 子任务生命周期状态机(主管维护)

```
pending(排队)
 └→ in_progress → design_done → gen_done → review_done → compile_done → packaged → delivered
          │             │退回         │退回         │编译失败修复中
          │             ▼            ▼            ▼
          └────── blocked(等用户) / failed(达上限)
```

状态变更写回 State 的 TodoList,CLI/Web 实时展示(参照 demo.html 6 步回显):
> 任务 A:采购审核插件(单据)— 审查退回 1 次,重新生成中
> 任务 B:库存服务插件(服务)— 编译通过 ✅
> 共 2 子任务,1 完成,1 进行中

### 5.4 审查裁决契约(w4 输出)

```
裁决: Approved | Needs fixes
Critical(必改,退回 w3)  / Important(应改,退回)  / Minor(记入交付包,不阻塞)
每条: 位置(file:line) + 问题 + 依据 + 修法
```

## 6. RAG 四库设计

| 库 | 内容 | 体量 | 检索方式 | 使用者 |
|---|---|---|---|---|
| API 参考库 | 类/方法签名、接口定义(Kingdee.BOS.*) | 大 | 向量 + BM25 混合,过滤命名空间/FormId | w2、w3、w4、w5 |
| 开发指南库 | 教程、示例代码,按插件类型标注 | 中 | 向量检索 + 插件类型过滤 | w2、w3 |
| 规范库 | 团队开发规范、审查规则 | 小 | 不检索,整库注入 prompt | w4、w7 |
| 经验库 | 踩坑记录、编译错误模式(w7 沉淀物) | 增长中 | 向量检索(错误信息/类型标签) | w5、w7 |

### 6.1 处理管线

```
官方文档 ─┐
          ├→ 清洗 → 分块(保留代码块完整)→ 嵌入 → 入库(带元数据)
内部资料 ─┘      ↑                     ↑
              文档结构分块          元数据标注:
                              · 库类型(API/指南/经验)
                              · 插件类型(单据/服务/列表)
                              · 命名空间/FormId · 版本
w7 沉淀 ──────────────→ 增量写入经验库/规范库(即时生效)
```

### 6.2 检索策略(按 worker 路由)

- 设计/生成:API 参考 + 指南(过滤插件类型)。
- 审查:规范库整库 + API 参考抽查。
- 编译修复:经验库按错误信息语义检索(命中相似错误直接给修复方向)。
- 混合检索:BM25 + 向量(EnsembleRetriever)— API 名是精确词,不能只靠语义;API 参考库 BM25 权重调高。

### 6.3 关键设计点

1. 规范库不走向量检索 — 体量小,整库注入更可靠,更新即时生效。
2. 经验库自生长 — w7 沉淀 = 团队知识资产积累。
3. 分块必须代码感知 — 金蝶示例代码块完整保留,拆坏代码是检索灾难。

## 7. w1 需求澄清(交互式,参考 superpowers brainstorming 改造)

- 一次一问,多选优先;问题队列按轮次推进;不猜。
- **元数据驱动提问**:先查元数据,问题带真实字段/操作选项,不让用户手打 FormId。
  ```
  用户:「给采购单审核加个库存校验」
  w1: 「查到这个单据,操作有:审核/保存/提交。校验在哪个触发?
       可选字段:数量/库存组织/仓库。要校验哪个?」
  ```
- **问题模板三套**(项目内 skill,`load_skill` 绑定 w1):
  - 单据插件问题集:触发操作、校验字段、拦截方式、联动单据
  - 服务插件问题集:服务入口、事务边界、异常回滚、调用方
  - 列表插件问题集:列表字段、操作按钮、过滤条件
- **用户确认门槛**:spec 不确认不进 w2 — 直接服务"返工少"成功标准。
- w1 双产物:spec(需求规格,后续 worker 验收基准)+ plan(子任务清单,派发依据)。

## 8. 错误处理

| 场景 | 处理 |
|---|---|
| 元数据查不到(FormId 不存在) | w1 输出缺失清单,主管暂停,问用户补充 |
| 金蝶 API 连不上 | 降级纯文本模式,交付物标"未验证" |
| 审查退回超限(3 轮) | 主管汇总意见,问用户拍板 |
| 编译超限(5 次) | 交付"未通过"包 + 错误日志,不硬标成功 |
| RAG 无结果 | 提示知识缺失,模型 + 元数据兜底 |
| w7 沉淀失败 | 不阻塞交付,记待沉淀队列 |
| worker 报 BLOCKED/NEEDS_CONTEXT | 主管判断:能补的补(补检索/元数据),不能的问用户 |

## 9. 测试

- 单元:w1 拆解/类型判定/元数据解析;各 worker 输出 schema 校验。
- 集成:fake 编译容器(预设错误序列)测修复循环;mock 金蝶 API 测退回流;复合需求测并行派发;任务契约状态机流转。
- E2E:真实容器编译 3 类型样例插件各一。
- RAG:w7 沉淀 → 检索命中验证。

## 10. 目录结构

```
agents/kingdee-plugin-agent/
├── CLAUDE.md                    # 按 dev-standards §6 模板
├── __init__.py
├── agent.py                     # Supervisor 图构建
├── cli.py                       # CLI 入口
├── api.py                       # Web 入口(FastAPI,参照 sentiment-query-agent)
├── graph/
│   ├── __init__.py
│   ├── state.py                 # 子任务池 State、TodoList、契约数据结构
│   ├── supervisor.py            # 主管节点:派发/编排/升级
│   └── workers/
│       ├── __init__.py
│       ├── w1_requirement.py    # 需求澄清(问题模板驱动)
│       ├── w2_design.py         # 设计(按类型分支)
│       ├── w3_generate.py       # 代码生成(按类型分支)
│       ├── w4_review.py         # 审查(按类型分支)
│       ├── w5_compile.py        # 编译修复循环
│       ├── w6_package.py        # 打包
│       └── w7_distill.py        # 知识沉淀
├── tools/
│   ├── __init__.py
│   ├── kingdee_api.py           # 金蝶 WebAPI 元数据客户端
│   ├── compile_client.py        # 编译容器 HTTP 客户端
│   └── package.py               # 交付包组装
├── skills/
│   ├── __init__.py
│   ├── loader.py                # load_skill(复用现有模式)
│   └── requirement-clarify/     # ★ 需求澄清问题模板(单据/服务/列表三套)
│       ├── SKILL.md
│       └── bill.md / service.md / list.md
└── prompts/                     # 各 worker prompt(common/prompts.py 加载)

common/rag.py                    # RAG 客户端:RAG 检索/沉淀写入/规范库
```

## 11. 复用与依赖

- `common/llm.py`、`common/prompts.py`、skill 方法论(`load_skill`)、web demo 模式、checkpointer。
- 新基建:金蝶 WebAPI 客户端、编译容器服务、RAG(common/rag.py + Chroma + BGE)。
- 编译镜像需团队提供金蝶 BOS DLL(注意授权合规)。

## 12. 风险与待确认

- 金蝶官方文档可爬性需验证(站点结构/登录墙)。
- 编译容器:金蝶 BOS 编译在 Linux 容器的兼容性(可能需 mono/.NET 兼容层或 Windows 容器)。
- 内部资料格式:markdown/word/pdf 混排,解析管线按实际样本调整。
- 第一阶段(w1)用户确认与澄清轮次上限:防止无限追问,默认上限 10 轮。
