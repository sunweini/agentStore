# 金蝶插件代码审查方法论(code-reviewer)

配合 w4 审查阶段使用。系统提示已注入本 skill 摘要,LLM 需要完整审查方法论与
类型审查重点时调用 `load_skill("code-reviewer")` 获取本文与三套类型要点。

## 目标

审查 w3 生成的 Plugin.cs,产出审查意见(review.json,逐条 findings)与裁决
(review_verdict: Approved / Needs fixes)。审查是**按依据找问题**,不是自由发挥:
每条意见必须能指出违反的规范/API 签名/模板基线,没有依据的问题不许写。

## 输入

- `code`:w3 产物 Plugin.cs(按类型分支 references/{bill,service,list}.md 补充审查重点)
- `standards`:规范库整库注入的团队规范文本(**逐条对照**)
- `plugin_type` / `title`:子任务类型与标题(裁决上下文)

## 审查流程

1. **规范库整库对照**:逐条核对代码是否违反注入的规范 —— 全库扫,不能只扫
   印象中相关的条目;每发现一条违反,记一条 finding 并写明依据(违反哪条规范)。
2. **API 抽查**:对代码中出现的 Kingdee.BOS.* API 与事件签名,与 API 参考库核对:
   - 重写签名与基类不匹配 = **Critical**;
   - using 引用缺失(基类所在命名空间)= **Critical**;
   - 引用不存在的字段/操作/方法 = **Critical**(假字段/假方法名)。
3. **模板基线比对**:与对应类型 templates/<type>/template.cs 骨架比对,
   模板外新增的方法/事件/结构必须有依据(设计文档/规范),无依据 = 记录问题。
4. **异常处理核对**(按类型重点):bill 拦拦截方式/异常骨架与设计一致;
   service 事务提交点/回滚补偿;list 批量行级异常反馈。
5. **按类型取检查清单**:调 `references/{bill,service,list}.md` 逐项核对该类型
   特有重点,不遗漏类型特定项。

## 输出契约(review.json,每条含)

```
{"severity": "Critical|Important|Minor", "line": 行号, "issue": 问题,
 "依据": 违反的规范/API 签名, "修法": 建议修法}
```

## 裁决规则(写回 review_verdict)

- 存在 Critical(必改)或 Important(应改)→ **Needs fixes**(退回 w3)
- 仅 Minor(记入交付包)或无问题 → **Approved**

## 踩坑与纪律

- **禁止凭空添加问题**:没有依据(规范/API/模板基线)支撑的意见一律不写 ——
  噪声意见会稀释真正的问题。
- **禁止漏报两类必查项**:模板占位符残留({{TOKEN}} 未渲染 = Critical)与
  事件签名错误(与基类/模板不匹配 = Critical)是最高频缺陷,必须逐行扫。
- **裁决由确定性规则计算**:LLM 只产 findings,不自行下裁决 ——
  存在任一 Critical/Important 即 Needs fixes,不因"整体看起来还行"放宽。
- **行号必须真实**:line 指代码行号,意见可被 w3 直接定位;行号错误 = 意见无效。

## 参考文件

- `references/bill.md` — 单据/表单插件审查重点(AbstractBillPlugIn)
- `references/service.md` — 服务插件审查重点(AbstractOperationServicePlugIn)
- `references/list.md` — 列表插件审查重点(AbstractListPlugIn)
