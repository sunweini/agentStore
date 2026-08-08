# 单据/表单插件生成要点(bill,AbstractBillPlugIn)

模板:templates/bill/template.cs(OnLoad/AfterDoOperation 事件签名、
AbstractBillPlugIn 继承、using Kingdee.BOS.Core.Bill.PlugIn 等引用)。

## 生成要点

- **模板优先**:bill/template.cs 骨架不变(OnLoad(EventArgs) /
  AfterDoOperation(AfterDoOperationEventArgs) 事件签名、AbstractBillPlugIn
  继承、using 引用),业务只填 {{BUSINESS_LOGIC}}。
- **指南参数化**:控件读写用元数据真实字段(如 FQty/库存组织,来自
  get_form_fields);拦截方式(硬拦截抛异常/软拦截提示)与提示文案
  取自设计文档 —— 文案照抄设计,不许临场改写。
- **冲突以模板为准**:设计要求的挂载点与模板事件不一致时(如设计要求
  BeforeDoOperation 而模板只有 AfterDoOperation),以模板事件为准并在注释说明
  冲突点与取舍原因。
- **联动单据**:读写其他单据的 FormId 与写入字段按设计实现,保留模板异常骨架
  (try/catch 边界、回滚/日志)。

## 自检清单

- [ ] 两个事件重写签名与模板完全一致(参数类型不改)
- [ ] 用到的每个字段都在设计/元数据中出现过
- [ ] 拦截提示文案与设计文档一致
- [ ] 无模板占位符残留({{TOKEN}} 全部渲染)
- [ ] 新增方法/结构有设计依据
