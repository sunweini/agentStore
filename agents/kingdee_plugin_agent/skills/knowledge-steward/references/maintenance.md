# 维护操作手册(maintenance)

知识库维护四类操作,全部幂等可重跑。按步骤执行,每步验证产出。

## 1. 种子增补(seed/compile_errors.json)

**何时做**:w7 沉淀之外,人工确认了新错误模式(如新类型插件、新版本 BOS API)。

**步骤**:

1. 打开 `agents/kingdee_plugin_agent/seed/compile_errors.json`;
2. 按蒸馏标准追加条目,字段固定:code / file_pattern / message / fix / source
   (source 填 "seed";file_pattern 缺省 ""):
   ```json
   {"code": "CS1002", "file_pattern": "", "message": "; 应输入", "fix": "检查语句结尾分号", "source": "seed"}
   ```
3. 幂等语义:`seed_load.py::load_seed_data` 按签名 `code|file_pattern` 查重,
   已存在跳过,重跑不产生重复条目;
4. 灌入(seed_load 有命令行入口,幂等可重跑):
   `python -m agents.kingdee_plugin_agent.seed.seed_load [--data-dir <dir>]`
   (默认 data/kingdee-rag,与 RagClient 默认一致;输出 "种子灌入完成:新增 N 条",
   二次运行 N=0 即幂等生效);
5. 验证:`tests/test_kingdee_agent.py` 中 seed_load 幂等断言 n>=N(当前 7)更新为新
   条数,跑 `pytest tests/ -q` 全绿后提交。

**注意**:文本格式必须与 ExperienceStore.propose 统一(`[code] message 修复:fix`),
种子即 w7 格式样本(seed_load.py 注释明示)。

## 2. 文档导入(官方文档爬取 / 内部资料 → 向量库)

**何时做**:金蝶官方文档更新、新指南沉淀、内部踩坑长文值得长期检索。

**步骤**:

1. 目标库选择:
   - 官方 API 参考/接口签名/事件参数 → `api_ref`;
   - 团队开发向导/操作流程/约定 → `guide`;
   - 错误修复经验 → 走 `experience`(propose → verify,不走直接导入);
2. 导入(RAG 导入管线 `tools/ingest.py`;代码感知分块 —— 代码围栏 ``` 整体
   保留,段落边界切块,元数据带 source/title/collection):
   - 单页(标题缺省从页面 `<title>`/`<h1>` 提取,失败打印明确原因、退出码非零):
     ```bash
     python -m agents.kingdee_plugin_agent.tools.ingest \
       --url <URL> --collection api_ref|guide [--title 可选]
     ```
   - 批量目录(递归 *.md,自动去 YAML frontmatter,相对路径作 source,
     单文件失败跳过继续):
     ```bash
     python -m agents.kingdee_plugin_agent.tools.ingest \
       --dir <目录> --collection guide
     ```
   - 内部 skill 文档(SKILL.md + references/*.md;模板类代码不入库):
     ```bash
     python -m agents.kingdee_plugin_agent.tools.ingest --seed-internal --collection guide
     ```
   - 数据目录默认 `data/kingdee-rag`,`--data-dir <dir>` 可改(与 RagClient 一致);
3. 幂等:按 metadata.source 查重,同 source 重跑新增 0(不产生重复条目);
   **批量模式全部失败才报错**,部分失败打印警告继续;
4. 验证:对每页取 1~2 个关键词查询 `hybrid_search`(api_ref 用 bm25_weight=0.7),
   确认正确命中且排序合理;再跑全套测试。

**注意**:guide 库按 plugin_type 过滤检索是 w2/w3 的既有契约,当前导入管线
元数据仅 source/title/collection,无 plugin_type —— 类型过滤检索会漏召回
外部导入的文档(内部 skill 文档已在各 skill 自身注入链,不受影响);
如需类型过滤,后续扩展 `--metadata key=value` 导入口令(待办)。

## 3. 规范库合并(standards)

**何时做**:新规范(编码规范/审查红线)经评审确认。

**步骤**:

1. 规范以纯 markdown 整库注入(不建向量索引),新文件放入 StandardsLoader
   扫描的目录(路径由 agent.py 的 build_graph 注入,无固定仓库路径;目录下
   所有 *.md 按文件名排序整库注入);
2. 8k token 预算:`inject_text(limit_tokens=8000)` 超预算自动截断,并在注入文本
   尾部标注"[已截断,剩余 N 个文件,请调用 guide_fallback 检索]";
3. 预算超限时的处理:**人工决定合并/精简规范文件**(删冗余、合并章节),
   不自动改写规范内容;运行时兜底走 `guide_fallback`(guide 库检索,
   **不是 standards 检索**,勿混淆);
4. 验证:审查阶段 w4 的注入文本包含新规范;超限场景跑一次确认截断标注出现。

**注意**:规范合并是人工决策,w7 只提议不自动合并;guide_fallback 的语义是
"降级兜底",不代表规范库可检索。

## 4. 经验库定期 review

**何时做**:节奏建议每周一次,或 w7 沉淀量达到 ~10 条时。

**步骤**:

1. 拉取全部 proposed 条目(`status="proposed"` 元数据查询,或客户端
   `ExperienceStore.search_related` 观察 unverified 标注);
2. 逐条按蒸馏标准判定:
   - 有复现/修法确认 → `ExperienceStore.verify(signature)` 翻转 verified
     (仅元数据翻转,文档与向量不动),补全真实修法(fix 占位文案必须替换);
   - 一次性/无法归因 → `ExperienceStore.archive(signature)` 归档(元数据
     status 置 archived,文档与向量不动;被 search_related 过滤,不再出现在
     w5 检索);
   - 不确定 → 保持 proposed,下轮再看;
3. 记录:review 结论与归档原因可写回条目 message 或单独 review 笔记;
4. 验证:verify 后 `search_related` 对应条目 confidence 变为 "verified";
   归档条目不再出现在检索结果。

**注意**:verify 只处理元数据,文档文本若有修正需走
`client.add_documents` 新条目 + 旧条目归档,不原地改文本。
