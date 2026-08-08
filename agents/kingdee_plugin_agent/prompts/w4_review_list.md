# 列表插件审查要点(AbstractListPlugIn)

- 规范库整库:逐条对照注入的规范文本,重点核对过滤条件、批量操作的行级异常反馈。
- API 抽查:PrepareFilterParameter(FilterArgs)/AfterBindData(EventArgs) 签名与 API 参考库核对;基类 AbstractListPlugIn。
- 事件签名核对:重写签名与基类不匹配 = Critical;字段映射用假字段 = Critical;按钮引用不存在的操作 = Critical。
- 模板基线:与 list/template.cs 比对,新增方法/事件需在意见中说明依据。
