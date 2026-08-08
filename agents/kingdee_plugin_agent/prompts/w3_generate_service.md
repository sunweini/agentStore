# 服务插件生成要点(AbstractOperationServicePlugIn)

- 模板优先:service/template.cs 骨架不变(AfterExecuteOperationTransaction 事件签名、AbstractOperationServicePlugIn 继承、Kingdee.K3.Core 引用),业务只填 {{BUSINESS_LOGIC}}。
- 指南参数化:服务入口/参数签名、事务边界(单操作内/跨操作)、异常回滚与补偿动作取自设计文档 + guide 检索。
- 冲突以模板为准:设计要求的服务入口与模板基类方法不一致时,以模板为准并在注释说明。
- 事务与异常:读-判-写事务边界与回滚点按设计落实,保留模板异常骨架(回滚/日志/提示文案)。
