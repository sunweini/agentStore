# 常见金蝶编译错误模式与修复

用法:收到 compile_errors 后,**先把每条错误归入下列模式之一,再按该模式的
根因链定位修复**。修完一类错误的根因,往往连带消除多条下游错误(级联)。

## 错误分类总览

| 模式 | 典型错误码 | 根因 | 修法 |
|---|---|---|---|
| A. 缺引用/命名空间 | CS0246 / CS0234 | using 缺失或程序集未引用 | 补 using / 补 csproj Reference |
| B. 签名与基类不匹配 | CS0506 / CS0115 | 事件重写签名与基类不一致 | 对齐模板/API 参考签名 |
| C. 名称/成员不存在 | CS0103 / CS1061 | 拼写错、作用域错、字段/方法名编造 | 核对元数据字段名/事件签名 |
| D. 语法与占位符残留 | CS1002 / CS1525 / CS1519 | 花括号不配对、{{TOKEN}} 未渲染 | 通读全文补渲染/修正语法 |
| E. 模板占位符残留 | (编译提示类/审查联动) | {{BUSINESS_LOGIC}} 等未渲染 | 渲染全部 {{TOKEN}} 后重编 |

---

## A. 缺引用/命名空间

### A1. CS0246 — 命名空间或类型找不到
- 根因:缺 using(Kingdee.BOS.Core 等)或程序集未引用。
- 修法:确认类型所在命名空间与程序集,补 using;缺程序集引用时 csproj 加
  Reference 到对应 dll(如 Kingdee.BOS.dll)。
- 经验条目(seed):"CS0246 命名空间或类型找不到(缺 Kingdee.BOS 引用)
  → csproj 加 Reference 到 Kingdee.BOS.dll"。

### A2. CS0246(服务插件变体)— 基类 AbstractOperationServicePlugIn 找不到
- 根因:缺 K3 Core 引用(基类在 Kingdee.K3.Core.dll)。
- 修法:引用 Kingdee.K3.Core.dll + 补 using Kingdee.K3.Core.ServiceHelper。
- 经验条目(seed,file_pattern=AbstractOperationServicePlugIn):
  "服务插件基类找不到(缺 K3 Core 引用)→ 引用 Kingdee.K3.Core.dll"。

### A3. CS0234 — 命名空间中不存在该类型
- 根因:命名空间路径拼错(如把 Kingdee.BOS.Core.Metadata 写成
  Kingdee.BOS.Metadata)。
- 修法:核对完整命名空间路径(Kingdee.BOS.Core.Metadata /
  Kingdee.BOS.Core.Bill.PlugIn / Kingdee.BOS.Core.List.PlugIn.Args 等),
  对照模板 template.cs 的 using 集。
- 经验条目(seed):"CS0234 命名空间中不存在该类型(命名空间拼错)
  → 核对 Kingdee.BOS.Core.Metadata 等完整命名空间"。

---

## B. 签名与基类不匹配

### B1. CS0506/CS0115 — 重写签名与基类不一致
- 根因:事件重写时参数类型/个数/返回类型与基类不匹配。
- 典型正确签名(以模板为准):
  - 单据:OnLoad(EventArgs e)、AfterDoOperation(AfterDoOperationEventArgs e)
  - 服务:AfterExecuteOperationTransaction(AfterExecuteOperationTransaction e)
  - 列表:PrepareFilterParameter(FilterArgs e)、AfterBindData(EventArgs e)
- 修法:逐字对齐 templates/<type>/template.cs 的签名,不要凭记忆写参数类型。
- 注意:签名错是**级联源头** —— 修复后重编通常连带消除一批 CS1061。

---

## C. 名称/成员不存在

### C1. CS0103 — 名称不存在(变量/方法拼写或作用域)
- 根因:变量/方法拼写错误、作用域外引用、字段名编造。
- 修法:核对元数据字段名/事件签名 —— 字段来自 get_form_fields,
  操作来自 get_operations,元数据查不到的名字 = 编造,必须替换为真实名。
- 经验条目(seed):"CS0103 名称不存在(变量/方法拼写或作用域)
  → 核对元数据字段名/事件签名"。

### C2. CS1061 — 对象不包含成员定义(方法名/事件名错)
- 根因:对某对象调用了不存在的成员(如把 AfterDoOperation 写成
  AfterOperation、事件参数属性名错)。
- 修法:用元数据/API 参考查询确认真实成员名与事件参数属性。
- 经验条目(seed):"CS1061 对象不包含成员定义(方法名/事件名错)
  → 用元数据查询确认事件名(如 AfterDoOperation)"。

---

## D. 语法与占位符残留

### D1. CS1002/CS1525/CS1519 — 语法错误
- 根因:花括号不配对、分号缺失、模板渲染后结构破坏。
- 修法:通读全文核对结构 —— 尤其 {{BUSINESS_LOGIC}} 渲染位置是否把
  方法体写坏(多余/缺失花括号);对照模板骨架恢复结构。

### D2. 模板占位符残留
- 根因:w3 未渲染全部 {{TOKEN}}({{BUSINESS_LOGIC}} 等残留)。
- 修法:渲染全部占位符后重编;这是 w4 审查必查项(w4 判 Critical),
  w5 见到残留同样先修这个 —— 残留占位符不是合法 C#,会引出 CS 语法/类型错误。
- 纪律:修复后自检全文不再含 {{TOKEN}} 模式。

---

## 修复纪律(对照 SKILL.md)

1. 先分类再动手:错误归入 A-E 模式,修根因不修表象。
2. 修复优先级:阻断性(缺引用/签名)→ 单点(拼写/成员名)→ 语法/残留;
   每轮只处理本轮错误,不重构无关逻辑。
3. 防重复提交:每轮真实改写后才写回重编(上层有防原样重提交校验,
   但每轮都应给出真实修改)。
4. 经验命中 → 修复后自核;经验未命中 → 按规范自行修复,不以此拒绝修复。
5. 编译通过后整段代码保持完整可用,不为消错删功能。
