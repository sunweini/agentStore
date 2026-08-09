// 列表插件模板:事件签名、基类继承、异常骨架(列表插件,官方文档核验)
using System;
using Kingdee.BOS.Core.List.PlugIn;
using Kingdee.BOS.Core.List.PlugIn.Args;
using Kingdee.BOS.Util;

namespace {{NAMESPACE}}
{
    public class {{CLASS_NAME}} : AbstractListPlugIn
    {
        public override void PrepareFilterParameter(FilterArgs e)
        {
            base.PrepareFilterParameter(e);
        }

        public override void AfterBindData(EventArgs e)
        {
            base.AfterBindData(e);
            // {{BUSINESS_LOGIC}}
        }
    }
}
