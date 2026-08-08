# 服务插件审查要点(AbstractOperationServicePlugIn)

- 规范库整库:逐条对照注入的规范文本,重点核对事务边界、回滚补偿、返回契约。
- API 抽查:AfterExecuteOperationTransaction 签名与 API 参考库核对;基类 AbstractOperationServicePlugIn 及 Kingdee.K3.Core 引用。
- 事件签名核对:重写签名与基类不匹配 = Critical;事务内提交点与设计不符 = Important;漏回滚补偿 = Critical。
- 模板基线:与 service/template.cs 比对,新增方法/事件需在意见中说明依据。
