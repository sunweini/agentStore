// 单据/表单插件模板:事件签名、基类继承、异常骨架(团队验证过的基准)
using Kingdee.BOS.Core.Bill.PlugIn;
using Kingdee.BOS.Core.Metadata;
using Kingdee.BOS.Util;

namespace {{NAMESPACE}}
{
    public class {{CLASS_NAME}} : AbstractBillPlugIn
    {
        public override void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
        }

        public override void AfterDoOperation(AfterDoOperationEventArgs e)
        {
            base.AfterDoOperation(e);
            // {{BUSINESS_LOGIC}}
        }
    }
}
