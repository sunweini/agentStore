# 单据/表单插件审查要点(AbstractBillPlugIn)

- 规范库整库:逐条对照注入的规范文本,重点核对拦截方式(硬/软拦截)、异常处理、提示文案。
- API 抽查:事件签名与 API 参考库核对——OnLoad(EventArgs)、AfterDoOperation(AfterDoOperationEventArgs);基类 AbstractBillPlugIn;控件读写 API 签名。
- 事件签名核对:重写签名与基类不匹配 = Critical;using 缺 Kingdee.BOS.Core.Bill.PlugIn = Critical;引用了不存在的假字段 = Critical。
- 模板基线:与 bill/template.cs 比对,新增方法/事件需在意见中说明依据。
