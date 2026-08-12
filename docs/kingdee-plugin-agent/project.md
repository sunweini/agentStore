# kingdee-plugin-agent 项目文档

> 金蝶云星空插件开发 Agent:输入自然语言需求,自动完成 澄清 → 设计 → 生成 → 审查 → 编译 → 冒烟 → 打包 → 沉淀 全流程,交付可部署的插件交付包。
>
> 本文面向决策者与新人,提供背景、目标、范围、架构概览、里程碑与规划;技术细节见 [tech.md](tech.md),上手用法见 [manual.md](manual.md)。代码实现以 `agents/kingdee_plugin_agent/CLAUDE.md` 为唯一事实来源,本文与其保持一致。

## 1. 背景与目标

### 1.1 金蝶云星空二开痛点

- **重复劳动**:每个二开需求都要人工走"理解需求 → 设计 → 写代码 → 编译 → 部署验证"全流程,大量工作与上一个需求高度相似(事件绑定、表单字段校验、按钮操作拦截是高频模式)。
- **API 复杂**:金蝶 BOS 插件开发涉及 WebAPI 元数据(FormId/字段/操作)、事件签名、基类继承(AbstractBillPlugIn / AbstractOperationServicePlugIn / AbstractListPlugIn)等大量 API 知识,学习与查错成本高。
- **编译门槛**:需要 .NET Framework 4.x + 金蝶 BOS DLL 环境才能编译验证,"代码写完但编译不过"是常态,编译环境配置本身就是一道门槛。

### 1.2 目标

把"给金蝶 X 单据加 Y 功能"的模糊需求,变成**经过设计/审查/编译/冒烟的插件交付物**(源码 + DLL + 部署说明 + 记录),并让踩坑自动沉淀进经验库,越用越准。

## 2. 成功标准

| 标准 | 含义 |
|---|---|
| 编译通过率高 | 编译是硬性质量门:生成完代码不算成功,编译通过(最好冒烟通过)才算交付 |
| 代码符合需求 | 交互式澄清(不猜需求)+ w4 审查 + 用户验收(accept/reject)三层符合性把关 |
| 返工少 | 全局返工预算 3 轮封顶,知识沉淀(经验库)让同一类坑不反复踩 |

## 3. 范围

- **插件类型**:单据/表单插件(bill)、服务插件(service)、列表插件(list),暂不含定时任务。
- **复合需求**:一个需求可拆多个子任务,支持依赖拓扑(pending 依赖者等依赖方交付后派发)与并行派发(并发 ≤3)。
- **全流程**:澄清 → 设计 → 生成 → 审查 → 编译修复 → 冒烟 → 打包 → 沉淀,任一环节失败按预算重工或收尾。
- **交互式澄清**:一次一问(≤10 轮),确认摘要列出"已确认决策 + 假设",用户确认后拆子任务;不认可时最多再确认 1 次,仍不确认则带假设强制收口。
- **知识沉淀**:编译错误与验收拒绝原因写入经验库(proposed 态,人工核验后转 verified),后续任务检索复用。

## 4. 架构概览

一句话:**1 个主管 + 8 个 worker 的 LangGraph 循环图** —— 主管负责拆解需求、按依赖拓扑派发、扣返工预算、判定终态;8 个 worker 各司一个流水线环节,并行子任务 ≤3。

```
需求 ──► 主管(supervisor)
           │
           ├─ 澄清交互(interrupt 挂起,w1 一次一问,确认后拆子任务)
           ├─ 派发(dispatcher,Send 并行 ≤3)──► w2设计 → w3生成 → w4审查
           │                                     │ w5编译 → w5.5冒烟 → w6打包 → w7沉淀
           └─ 终态:全部交付 → finish / 失败或预算耗尽 → fail
                          └──► 交付包(zip)+ TodoList 摘要
```

- 主管循环:依赖失败传递 → 终态检查 → 就绪批派发 → LLM 决策/确定性兜底。
- 8 个 worker:w1 需求澄清(交互节点)/ w2 设计 / w3 代码生成 / w4 审查 / w5 编译修复 / w5.5 部署冒烟 / w6 打包 / w7 知识沉淀。
- 两个入口:**CLI**(`python -m agents.kingdee_plugin_agent.cli`)+ **Web API**(FastAPI + SSE 实时进度 + 演示页)。

## 5. 里程碑状态

### 5.1 已完成

三个实现 plan 全部交付(Plan A 编译服务 / Plan B 知识基建 / Plan C 编排与入口),此后 v1.9~v1.13 持续补强,**E2E 门已达成**(三类型样例真实编译通过),当前 **212 项测试全过**(记录于 CHANGELOG v1.13.0,含图全链路、CLI、API、RAG、模板、编译服务、eval 集)。

| 里程碑 | 内容 | 状态 |
|---|---|---|
| Plan A 编译服务 | 编译 HTTP 服务(mock/msbuild 双后端)、错误解析器、编译客户端、Dockerfile/docker-compose | ✅ 交付 |
| Plan B 知识基建 | RAG 四库、混合检索(BM25+向量)、经验库两态+去重、三类型模板、金蝶 WebAPI 客户端、冒烟/打包工具 | ✅ 交付 |
| Plan C 编排与入口 | 1 主管 + 8 worker 循环图、任务契约、CLI + Web API + 演示页、skill 体系(6 个) | ✅ 交付 |
| v1.9 时间预算 + 需求版本冻结 | 全流程 30min 图级总闸(started_at,设计 §8);spec 确认即冻结(spec_version 盖章 + API 409 锁 + 交付包 records/spec.json) | ✅ 交付 |
| v1.10 P2 五项 | 指标随 State 统计(TaskState.metrics 五计数器)+ OTel span(低基数)、失败收尾"未完成"包(w6_fail → deliverable-failed-*.zip)、LLM 畸形 JSON 重试(2 次尝试)、交付包 records 接线(design/review 进包)、.env 配置组 | ✅ 交付 |
| v1.11 冒烟链路 + 反馈通道 | 冒烟链路结构级修复(FormId 提取 + DLL 传递,验证对象改 DLL)、反馈端点 POST /tasks/{id}/feedback(经验库 DEPLOY 通道)、`--env` 记录进 state.environment | ✅ 交付 |
| v1.12 下发模板字段 | 验收标准(Subtask.acceptance_criteria,w4 审查对照)+ 子任务退回上限(Subtask.max_rework/rework_count,超限子任务 failed 而非 needs_rework) | ✅ 交付 |
| v1.13 E2E 门达成 | 三类型模板真实编译修复(using System / 命名空间 / 删除假引用)+ 旧式 csproj 兼容 Framework MSBuild(msbuild_path 探测 + target_framework 可配 + 180s 超时)+ DLL persist 时序修复;**bill/service/list 三类型样例在 Windows Server 2016 金蝶服务器(WebSite\bin 真实 DLL + .NET 4.8 DevPack + Framework MSBuild)全部编译通过并产出 DLL** —— 里程碑 1 启动门达成 | ✅ 达成 |

### 5.2 待办(未验证项)

以下均为**未验证/未达成**项,上线前需推进:

- ~~**真实金蝶 WebAPI 联调**~~(**✅ 已达成 2026-08-10**,10.33.17.130 真实实例:ValidateUser 登录 / ExecuteBillQuery / QueryBusinessInfo 三端点可用,`get_form_fields` 真实返回 337 字段,会话失效自动重登;官方 SDK 无 GetFormOperations/QueryBusinessObjects,占位方法已删,见 tech.md §11)。
- **RAG 内容**:guide/api_ref 已接真实资料 —— 内部 skill 文档 + 金蝶官方 9 页已灌入(RAG 导入管线 `tools/ingest.py`,2026-08-09,guide 65 chunks / api_ref 4 chunks,检索冒烟通过,见 CHANGELOG v1.14.0);剩余:**standards 规范库目录**仍以模板要点为主,待接真实编码规范;外部导入文档暂无 plugin_type 元数据,类型过滤检索需扩展导入口令。
- ~~**线上 DeepSeek 验证 load_skill 绑定**~~(**✅ 已达成 2026-08-10**:首选 `with_structured_output(schema, tools=[load_skill])` 形态被 DeepSeek 拒绝后自动回退 JSON Mode,回退路径实测可用,见 tech.md §11)。
- **v1 已知债务**(见 tech.md §11):内存任务存储 / apikey timing-safe / msgpack 白名单已清偿(v1.21.0);剩余:**`--env` 部分消费**(进 requirement_spec + `state.environment["env_name"]` + 凭证分套,未做节点级环境差异化,单环境 v1)、CLI 门控仅查 KD_BASE_URL。

## 6. 后续规划

- **知识自生长**:经验库"种子 + w7 沉淀 + 人工 review"滚动运转,proposed → verified 流转,编译修复命中率持续提升;错误映射单一来源经验库(动态),skill 只含方法论。
- **多环境支持**:`--env` 已记录进 requirement_spec + `state.environment["env_name"]`(v1.11,节点可感知),未做环境级差异化(单环境 v1);后续按环境隔离金蝶配置与数据目录。
- **交付包合并**:多子任务 v1 逐包交付,后续合并为单一 zip。
- ~~**任务持久化与限流**~~(**✅ 已达成 v1.21.0**:任务落盘 SQLite(同步 SqliteSaver + 元数据表)重启恢复 + 并发闸门 Semaphore(429),恢复路径非阻塞 acquire 防死锁)。
- **其他 ERP 扩展方向**:金蝶能力已封装在 `tools/kingdee_api.py`(客户端)+ `templates/`(类型模板)+ `compile_service`(编译容器),agent 编排层与金蝶细节解耦,具备向同类 ERP(BOS 系)或新插件类型横向扩展的形态;扩展时优先补模板/检索库而非改图。

## 7. 技术栈

Python + LangChain/LangGraph 1.2.10(循环图,interrupt/send/Command)+ DeepSeek(经 ChatOpenAI,OpenAI 兼容 API,`common/llm.py` 多供应商注册表)+ FastAPI/SSE(Web 入口)+ Chroma + BGE 中文嵌入(本地,`common/rag.py` 混合检索)+ .NET msbuild 编译容器(FastAPI 包装)。
