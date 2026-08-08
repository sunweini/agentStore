# 列表插件生成要点(AbstractListPlugIn)

- 模板优先:list/template.cs 骨架不变(PrepareFilterParameter/AfterBindData 事件签名、AbstractListPlugIn 继承),业务只填 {{BUSINESS_LOGIC}}。
- 指南参数化:列表字段映射(get_form_fields)、操作按钮(get_operations)、默认过滤条件与批量逐行处理语义取自设计文档。
- 冲突以模板为准:设计要求的挂载方法与模板事件不一致时,以模板为准并在注释说明。
- 批量异常:逐行失败行级反馈与提示文案按设计落实,不破坏模板整体骨架。
