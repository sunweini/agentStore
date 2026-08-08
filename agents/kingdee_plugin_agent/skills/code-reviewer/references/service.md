# 服务插件审查重点(service,AbstractOperationServicePlugIn)

模板基线:templates/service/template.cs(AfterExecuteOperationTransaction
(AfterExecuteOperationTransaction)/AbstractOperationServicePlugIn/
using Kingdee.K3.Core.ServiceHelper,基类在 Kingdee.K3.Core.dll)。

## 审查重点

- **规范库整库**:逐条对照注入的规范文本,重点核对事务边界、回滚补偿、返回契约。
- **API 抽查**:AfterExecuteOperationTransaction 签名与 API 参考库核对;
  基类 AbstractOperationServicePlugIn 及 Kingdee.K3.Core 引用。
- **事件签名核对**:
  - 重写签名与基类不匹配 = **Critical**;
  - 事务内提交点与设计不符(设计说读-判-写同一事务,代码拆开)= **Important**;
  - 漏回滚补偿(部分成功无补偿动作)= **Critical**;
  - using 缺 Kingdee.K3.Core(CS0246 服务插件基类找不到)= **Critical**。
- **模板基线**:与 service/template.cs 比对,新增方法/事件需在意见中说明依据。

## 必查清单

- [ ] 事务边界与设计一致(不缩窄不扩大)
- [ ] 部分成功有补偿动作(缺 = Critical);回滚点与设计一致
- [ ] 返回契约(成功/失败码+消息)按设计实现
- [ ] 模板占位符残留逐行扫(残留 = Critical)
- [ ] 提示文案与设计文档一致
