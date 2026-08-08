# 列表插件生成要点(list,AbstractListPlugIn)

模板:templates/list/template.cs(PrepareFilterParameter/AfterBindData 事件签名、
AbstractListPlugIn 继承、using Kingdee.BOS.Core.List.PlugIn 等引用)。

## 生成要点

- **模板优先**:list/template.cs 骨架不变(PrepareFilterParameter(FilterArgs) /
  AfterBindData(EventArgs) 事件签名、AbstractListPlugIn 继承),业务只填
  {{BUSINESS_LOGIC}}。
- **指南参数化**:列表字段映射(get_form_fields)、操作按钮(get_operations)、
  默认过滤条件与批量逐行处理语义取自设计文档 —— 字段与按钮必须真实存在,
  禁止出现设计外的假字段。
- **冲突以模板为准**:设计要求的挂载方法与模板事件不一致时(如设计要求
  BeforeBindData 而模板只有 AfterBindData),以模板为准并在注释说明。
- **批量异常**:逐行失败行级反馈与提示文案按设计落实,不破坏模板整体骨架;
  整体回滚 vs 逐行成功累积的语义与设计一致。

## 自检清单

- [ ] PrepareFilterParameter/AfterBindData 重写签名与模板完全一致
- [ ] 列表字段/操作按钮全部来自元数据(设计出现过)
- [ ] 默认过滤条件与设计一致
- [ ] 批量失败行级反馈语义与设计一致(整体回滚/逐行累积)
- [ ] 无模板占位符残留;新增方法/结构有设计依据
