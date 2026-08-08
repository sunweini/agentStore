# 列表插件审查重点(list,AbstractListPlugIn)

模板基线:templates/list/template.cs(PrepareFilterParameter(FilterArgs)/
AfterBindData(EventArgs)/AbstractListPlugIn/using Kingdee.BOS.Core.List.PlugIn)。

## 审查重点

- **规范库整库**:逐条对照注入的规范文本,重点核对过滤条件、批量操作的
  行级异常反馈。
- **API 抽查**:PrepareFilterParameter(FilterArgs)/AfterBindData(EventArgs)
  签名与 API 参考库核对;基类 AbstractListPlugIn。
- **事件签名核对**:
  - 重写签名与基类不匹配 = **Critical**;
  - 字段映射用假字段(元数据查不到)= **Critical**;
  - 按钮引用不存在的操作(get_operations 中查不到)= **Critical**。
- **模板基线**:与 list/template.cs 比对,新增方法/事件需在意见中说明依据。

## 必查清单

- [ ] 批量失败处理语义与设计一致(整体回滚 vs 逐行累积)
- [ ] 行级反馈与提示文案与设计一致
- [ ] 默认过滤条件与设计一致
- [ ] 模板占位符残留逐行扫(残留 = Critical)
- [ ] 字段/按钮全部真实(元数据可查)
