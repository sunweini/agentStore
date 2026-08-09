// 服务插件模板:事件签名、基类继承、异常骨架(操作服务插件)
// 基类 AbstractOperationServicePlugIn 在 Kingdee.BOS.Core.dll
// (命名空间 Kingdee.BOS.Core.DynamicForm.PlugIn,真实环境反射验证)
using System;
using Kingdee.BOS.Core.DynamicForm.PlugIn;
using Kingdee.BOS.Core.DynamicForm.PlugIn.Args;
using Kingdee.BOS.Util;

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
