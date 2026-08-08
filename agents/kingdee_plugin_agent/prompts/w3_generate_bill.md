# 单据/表单插件生成要点(AbstractBillPlugIn)

- 模板优先:bill/template.cs 骨架不变(OnLoad/AfterDoOperation 事件签名、AbstractBillPlugIn 继承、using 引用),业务只填 {{BUSINESS_LOGIC}}。
- 指南参数化:控件读写用元数据真实字段(如 FQty/库存组织,来自 get_form_fields);拦截方式(硬拦截抛异常/软拦截提示)与提示文案取自设计文档。
- 冲突以模板为准:设计要求的挂载点与模板事件不一致时,以模板事件为准并在注释说明。
- 联动单据:读写其他单据的 FormId 与写入字段按设计实现,保留模板异常骨架(try/catch 边界、回滚/日志)。
