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
   均为**已真实环境编译验证的基准**(三类型模板已在 Windows + 金蝶 BOS DLL
   环境编译通过并产出 DLL),**不改模板骨架**,业务实现只填入 {{BUSINESS_LOGIC}}
   等占位符位置。
2. **指南参数化**:业务细节取自设计文档 + guide 检索(插件类型过滤);
   字段/操作/API 签名必须真实(来自元数据/指南),禁止编造 —— 无来源的外部
   API(库存查询/服务调用等)一律 TODO 占位,标准见「禁止编造 API」一节。
3. **冲突裁决**:设计或指南与模板冲突时**以模板为准**,在代码注释中标注
   冲突点与取舍原因(不标注 = w4 审查扣"模板外改动无依据")。
4. **占位符清零**:落盘前自检全部 {{TOKEN}} 均已渲染,不残留模板占位符
   (残留 = w4 确定性骨架判 Critical)。
5. **验收自检**:using 引用完整(基类所在命名空间)、事件签名与模板一致、
   业务逻辑与设计逐条对应、不引入模板外结构、无来源外部 API 均为 TODO
   占位(未凭记忆补写签名)。

## 输出契约

- 产物:完整可编译的 Plugin.cs(整体写回 store)。
- 代码质量底线:模板事件签名一个不改;新增方法/结构需与设计对应;
  异常处理沿用模板骨架(try/catch 边界、回滚/日志),不临场重构。

## 踩坑与纪律

- **改模板骨架 = 浪费整条流水线**:w4 审查按模板基线比对,改了骨架会被逐条
  追问依据;事件签名错一个,编译必挂(CS0506/CS0115 基类重写不匹配)。
- **假字段/假方法名(外部 API 同罪,且是最贵的一种)**:CS0103/CS1061 的头号
  来源;拿不准的字段回元数据查,查不到标注释 TODO 也不许编造。库存查询/服务
  调用类 API 凭记忆补全 = 编译必挂 + 烧掉整条编译-修复循环,坏例/好例对比见
  「禁止编造 API」一节。
- **业务逻辑与设计脱节**:w4 逐条对照设计审查,设计里有而代码里没有 = 漏实现
  (Important),代码里有而设计没有 = 无依据改动(也是问题)。
- **占位符残留是最高频败因**:{{BUSINESS_LOGIC}} 未渲染直接判 Critical,
  生成完成后通读一遍全文再落盘。

## 禁止编造 API(签名必须有来源)

外部 API 的方法名/参数/返回类型(库存查询、服务调用、WebAPI 接口),**必须有
来源**:guide 检索命中、元数据(get_form_fields / 接口确认)命中、模板既有调用。
三者皆无 → 一律 TODO 占位,禁止凭记忆补全。判定标准一句话:签名能说出处
(guide 哪一节 / 元数据哪个接口 / 模板哪一行)才写;说不出处 = 没来源 = TODO。

### 坏例:编造 API(编译必挂,注释还让它显得像真的)

```csharp
// 需求:审核时校验库存,不足则拦截提示
// 坏例:InvServiceHelper.QueryInvQty / InvQueryParam / InvQueryResult.AvailableQty
// 全部凭记忆编造 —— 参数与返回类型注释得越"真实",越难被审查发现,
// 但 CS1061/CS0103 编译必挂
var param = new InvQueryParam();                     // 编造的类型,不存在
param.MaterialId = bill.MaterialId;
param.StockOrgId = orgId;
InvQueryResult result = InvServiceHelper.QueryInvQty(param);   // 编造的方法,不存在
if (result.AvailableQty < bill.Qty) throw new Exception("库存不足");  // 编造的属性
```

### 好例:TODO 占位(编译通过,后续元数据接线补全)

```csharp
// 需求:审核时校验库存,不足则拦截提示
// 好例:库存查询 API 签名未在元数据/guide 检索命中 —— 禁止编造,留 TODO 占位
// TODO(接线):检索到库存查询服务的真实方法签名与返回字段后补全;
// 未接线前返回默认值,不影响编译
var availableQty = 0;      // 默认值占位,签名确认后替换为真实查询结果
// TODO: 签名确认后在此接入库存校验(不足则提示),现骨架占位
```

### 为什么编造 API 是最贵的错

- **编造 → 编译失败 → 进 w5 修复轮(≤5 轮,吃返工预算)→ 修不好触发重新生成**:
  一次编造烧掉整条编译-修复循环,还可能连带 w4 审查打回、拖慢整个任务;
- **TODO 占位零成本**:编译直接通过,后续由元数据/真实环境接线补全,
  不占 w5 轮次与返工预算;
- **带注释的假 API 与真代码无法区分**:参数/返回类型注释齐全的编造调用,
  审查与人工都拦不住,唯一防线就是"无来源不写"这条纪律本身。

## 参考文件

- `references/bill.md` — 单据/表单插件生成要点(AbstractBillPlugIn)
- `references/service.md` — 服务插件生成要点(AbstractOperationServicePlugIn)
- `references/list.md` — 列表插件生成要点(AbstractListPlugIn)
