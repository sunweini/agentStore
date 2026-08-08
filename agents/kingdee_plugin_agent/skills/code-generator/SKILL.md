# 金蝶插件 C# 代码生成方法论(code-generator)

配合 w3 生成阶段使用。系统提示已注入本 skill 摘要,LLM 需要完整生成方法论与
类型要点时调用 `load_skill("code-generator")` 获取本文与三套类型规范。

## 目标

基于设计文档(w2 产物)与类型模板(templates/<type>/template.cs),生成可直接
提交编译的插件 C# 代码,落盘 Plugin.cs。生成质量的底线:**模板骨架原样保留、
全部 {{TOKEN}} 渲染清零、业务逻辑与设计逐条对应** —— 三条任何一条不满足,
w4 审查必然打回。

## 输入

- `design`:w2 设计文档(design.md,含验收自检与类型检查清单结论)
- `template`:本类型的模板源码(load_template 产出,templates/<type>/template.cs)
- RAG guide 检索(按 plugin_type 过滤):字段/操作/API 签名的补充依据

## 流程步骤

1. **模板优先**:以模板为基准 —— 基类继承(AbstractBillPlugIn /
   AbstractOperationServicePlugIn / AbstractListPlugIn)、事件签名、using 引用
   均为团队验证过的基准,**不改模板骨架**,业务实现只填入 {{BUSINESS_LOGIC}}
   等占位符位置。
2. **指南参数化**:业务细节取自设计文档 + guide 检索(插件类型过滤);
   字段/操作/API 签名必须真实(来自元数据/指南),禁止编造。
3. **冲突裁决**:设计或指南与模板冲突时**以模板为准**,在代码注释中标注
   冲突点与取舍原因(不标注 = w4 审查扣"模板外改动无依据")。
4. **占位符清零**:落盘前自检全部 {{TOKEN}} 均已渲染,不残留模板占位符
   (残留 = w4 确定性骨架判 Critical)。
5. **验收自检**:using 引用完整(基类所在命名空间)、事件签名与模板一致、
   业务逻辑与设计逐条对应、不引入模板外结构。

## 输出契约

- 产物:完整可编译的 Plugin.cs(整体写回 store)。
- 代码质量底线:模板事件签名一个不改;新增方法/结构需与设计对应;
  异常处理沿用模板骨架(try/catch 边界、回滚/日志),不临场重构。

## 踩坑与纪律

- **改模板骨架 = 浪费整条流水线**:w4 审查按模板基线比对,改了骨架会被逐条
  追问依据;事件签名错一个,编译必挂(CS0506/CS0115 基类重写不匹配)。
- **假字段/假方法名**:CS0103/CS1061 的头号来源;拿不准的字段回元数据查,
  查不到标注释 TODO 也不许编造。
- **业务逻辑与设计脱节**:w4 逐条对照设计审查,设计里有而代码里没有 = 漏实现
  (Important),代码里有而设计没有 = 无依据改动(也是问题)。
- **占位符残留是最高频败因**:{{BUSINESS_LOGIC}} 未渲染直接判 Critical,
  生成完成后通读一遍全文再落盘。

## 参考文件

- `references/bill.md` — 单据/表单插件生成要点(AbstractBillPlugIn)
- `references/service.md` — 服务插件生成要点(AbstractOperationServicePlugIn)
- `references/list.md` — 列表插件生成要点(AbstractListPlugIn)
