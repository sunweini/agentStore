# 单据/表单插件审查重点(bill,AbstractBillPlugIn)

模板基线:templates/bill/template.cs(OnLoad(EventArgs)/AfterDoOperation
(AfterDoOperationEventArgs)/AbstractBillPlugIn/using Kingdee.BOS.Core.Bill.PlugIn)。

## 审查重点

- **规范库整库**:逐条对照注入的规范文本,重点核对拦截方式(硬/软拦截)、
  异常处理、提示文案(与设计文档一致)。
- **API 抽查**:事件签名与 API 参考库核对 ——
  OnLoad(EventArgs)、AfterDoOperation(AfterDoOperationEventArgs);
  基类 AbstractBillPlugIn;控件读写 API 签名。
- **事件签名核对**:
  - 重写签名与基类不匹配 = **Critical**;
  - using 缺 Kingdee.BOS.Core.Bill.PlugIn = **Critical**;
  - 引用了不存在的假字段(元数据中查不到)= **Critical**。
- **模板基线**:与 bill/template.cs 比对,新增方法/事件需在意见中说明依据
  (设计文档对应小节),无依据 = 记录问题。

## 必查清单

- [ ] 拦截方式与设计一致(硬拦截抛异常位置/软拦截提示位置)
- [ ] 提示文案与设计文档逐字一致(临场改写 = Important)
- [ ] 联动单据 FormId/写入字段真实且与设计一致
- [ ] 模板占位符残留逐行扫(残留 = Critical)
- [ ] 异常骨架保留(try/catch 边界、回滚/日志未被破坏)
