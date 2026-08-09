# 沉淀质量标准(distillation)

经验库条目质量判据:能直接指导 w5 修复的才是好条目。沉淀前对照本节。

## 条目模板

统一格式(与种子 seed/compile_errors.json、ExperienceStore.propose 一致):

```
[错误码] 错误信息 修复:修法
```

字段语义:

- `错误码`:编译错误码(CSxxxx),检索主键,必须真实存在;
- `错误信息`:message 的稳定特征(稳定的那部分,不要带文件路径/行号/随机后缀);
- `修法`:可复现的修复配方 —— 改哪个文件、加什么引用、对齐什么签名,
  **"待验证"不是修法**。

### 验证字段(proposed 态必填)

proposed 条目追加 `验证:` 字段:

- **复现方式**:如"真实编译环境复现 CS0246 后按修法修复通过";
- 或**人工确认人**:如"由 XX 工程师审阅确认";

种子条目为 verified 基线,无需携带;verify 翻转后字段保留,作为追溯记录。

**想不出验证路径 → 这条不沉淀**:无法复现、无人能确认的观察(一次性错误、
无法归因的现象)永远停在 proposed,检索只添噪音 —— 沉淀了就是污染。

### 好例

```
[CS0246] 命名空间或类型找不到(缺 Kingdee.BOS 引用) 修复:csproj 加 Reference 到 Kingdee.BOS.dll
[CS0506] 不是重写,基类中不存在该成员(事件签名不匹配基类) 修复:核对基类事件签名并完全匹配(如 OnLoad(EventArgs e) / AfterDoOperation(AfterDoOperationEventArgs e)),模板 templates/<type>/template.cs 有基准
[CS0246] 命名空间或类型找不到(缺 K3 Core 引用) 修复:引用 Kingdee.K3.Core.dll 验证:真实编译环境复现 CS0246 后补 K3 Core 引用,重编译通过
```

可复现判据:错误码 + message 稳定特征 + 修法三要素齐全;同场景重犯时能命中。

### 坏例

```
[CS0246] 命名空间或类型找不到 修复:w7 沉淀,待人工验证
```

- fix 是占位文案 → 无修复配方,w5 命中后仍无从下手(当前 w7 运行时产物即此形态,
  属已知取舍:proposed 占位先行,verify 时必须补全修法);

```
[CS0103] .../Plugin.cs(42): 名称 'fdnMianField' 不存在
```

- message 带文件路径与行号 → 不可复现特征,检索时噪声;只保留稳定特征
  ("名称不存在(变量/方法拼写或作用域)")。

```
[CS1502] 环境网络抖动导致编译服务超时
```

- 一次性错误 → 不沉淀(无根因链,沉淀了只污染检索)。

```
[CS0234] 命名空间中不存在该类型 修复:可能是命名空间写错,待确认 验证:无
```

- 无验证路径 → 不沉淀:无法复现、无人确认,永远停在 proposed,检索只添噪音;
  想不出验证字段填什么,这条就先不要沉淀。

## 去重边界

- 签名 = `code|file_pattern`,ExperienceStore.propose 按签名精确查重
  (filter={"signature": sig}),同签名已存在 → 不重复入库;
- 同 code 不同 file_pattern = 两条独立条目(如 "CS0246|" 与 "CS0246|AbstractOperationServicePlugIn"
  分别对应"缺 BOS 引用"与"缺 K3 Core 引用",种子即此形态);
- 恒空 file_pattern 会让同 code 所有条目共享签名 "CS0246|" —— 后续不同场景的
  坑会被去重吞掉;需要区分场景时把区分位放进 file_pattern(如按插件类型
  "CS0246|bill");
- 验收拒绝走同一通道,但 file_pattern 必须带拒绝原因 sha256 摘要:
  ExperienceStore 按 "code|file_pattern" 去重,恒空 file_pattern 会让所有拒绝
  原因共享同一签名被吞并(api.py acceptance 已实现摘要入签名)。

## 签名规则速记

| 场景 | code | file_pattern | 签名 |
|---|---|---|---|
| 通用编译错误(种子/w7) | CS0246 | "" | "CS0246\|" |
| 类型化错误(种子) | CS0246 | AbstractOperationServicePlugIn | "CS0246\|AbstractOperationServicePlugIn" |
| 验收拒绝(api.py) | ARTIFACT | sha256(拒绝原因) | "ARTIFACT\|<sha256>" |
| 场景区分(人工增补) | CS0103 | bill | "CS0103\|bill" |

## proposed → verified 判据

proposed 可翻转 verified 的任一条件(满足其一即可):

1. **复现验证**:真实编译环境复现同 code+message,按条目修法修复成功;
2. **人工确认**:工程师审阅后确认根因链与修法正确;
3. 不确定 → 保持 proposed(检索时标注 unverified,仅供参照)。

propose 时填的验证字段 = review 对照的作业清单:翻转前照字段复现或找字段
写明的确认人确认;验证字段对不上 → 该条目还不到 verify 时候。

verify 只翻转元数据 status(文档与向量不动);归档(rejected/archived)条目
不进 search_related 检索(ExperienceStore 只返回 proposed/verified)。
