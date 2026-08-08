// 服务插件模板:事件签名、基类继承、异常骨架(操作服务插件,官方文档核验)
// 基类 AbstractOperationServicePlugIn 在 Kingdee.K3.Core.dll(缺引用报 CS0246)
using Kingdee.BOS.Core.DynamicForm.PlugIn.Args;
using Kingdee.BOS.Util;
using Kingdee.K3.Core.ServiceHelper;

namespace {{NAMESPACE}}
{
    public class {{CLASS_NAME}} : AbstractOperationServicePlugIn
    {
        public override void AfterExecuteOperationTransaction(AfterExecuteOperationTransaction e)
        {
            base.AfterExecuteOperationTransaction(e);
            // {{BUSINESS_LOGIC}}
        }
    }
}
