# 服务插件生成要点(service,AbstractOperationServicePlugIn)

模板:templates/service/template.cs(AfterExecuteOperationTransaction 事件签名、
AbstractOperationServicePlugIn 继承、using Kingdee.K3.Core.ServiceHelper 等引用;
基类在 Kingdee.K3.Core.dll,缺引用报 CS0246)。

## 生成要点

- **模板优先**:service/template.cs 骨架不变(AfterExecuteOperationTransaction
  (AfterExecuteOperationTransaction) 事件签名、AbstractOperationServicePlugIn
  继承、Kingdee.K3.Core 引用),业务只填 {{BUSINESS_LOGIC}}。
- **指南参数化**:服务入口/参数签名、事务边界(单操作内/跨操作)、异常回滚与
  补偿动作取自设计文档 + guide 检索 —— 事务边界与回滚点照设计落实,
  不许自行缩窄(如把设计的事务外校验挪进事务)。
- **冲突以模板为准**:设计要求的服务入口与模板基类方法不一致时,以模板为准
  并在注释说明。
- **事务与异常**:读-判-写事务边界与回滚点按设计落实,保留模板异常骨架
  (回滚/日志/提示文案);部分成功的补偿动作按设计实现,缺补偿 = 审查 Important。

## 自检清单

- [ ] AfterExecuteOperationTransaction 重写签名与模板完全一致
- [ ] 事务边界/回滚点与设计文档一致(不缩窄不扩大)
- [ ] Kingdee.K3.Core 相关 using 引用齐全(防 CS0246 服务插件基类找不到)
- [ ] 返回契约(成功/失败码+消息)按设计实现
- [ ] 无模板占位符残留;新增方法/结构有设计依据
