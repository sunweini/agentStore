# RAG 导入管线 + 集合灌入报告(kingdee-plugin-agent)

日期:2026-08-09 | commit:见 git log | 配套:CHANGELOG v1.14.0

## 1. 管线设计(agents/kingdee_plugin_agent/tools/ingest.py)

双入口 + CLI,零新增依赖(环境未装 beautifulsoup4,HTML→文本用 stdlib
`html.parser`;HTTP 用已有 httpx;存储复用 `common/rag.py` RagClient)。

```
URL ──► fetch_html(httpx,30s 超时,浏览器 UA)──► html_to_text(剔除
        script/style/nav/header/footer,块级标签转段落换行;pre 感知清洗:
        代码行原样保留缩进,非代码行折叠空白 + 剔样板行[分享/收藏/评论/
        翻页/导航/浏览计数类])
                                             └─► code_aware_chunk(1500)
                                             └─► RagClient.add_documents
                                                   metadata={source,title,collection}
目录 ─► 递归 *.md ─► strip_frontmatter(YAML)──► 同上(metadata={source:相对路径,title})
```

- **code_aware_chunk**:段落边界切块;代码围栏(```)无论多长、中间多少空行,
  整体独占一个 chunk,绝不在围栏内部切分(未闭合围栏也整块保留);超长单段落
  按句末标点(。！？!?;；)兜底切分 —— 只有围栏是硬性不切规则。
- **normalize_title**:&lt;title&gt;(剥离站点名后缀如 " - 金蝶开发者社区")→
  首个 &lt;h1&gt; → URL 尾段,三级回退;ingest_url 内部复用已抓取 html 不二次请求。
- **&lt;pre&gt; 代码块保留**:HTML 提取按 pre 感知 —— 代码行原样保留缩进/结构
  (非代码行折叠空白);浏览/赞赏计数等动态行(两次抓取数值不同)按样板剔除,
  保证同 URL 重跑文本稳定。
- **幂等是去重式,不是同步式**:按 source + 文本查重(Chroma get where),
  **同 source 且内容未变重跑新增 0;内容变更后重跑会新增、新旧版本并存** ——
  编辑已灌入文档须 `--delete-source <source>` 删旧再重灌(对齐 knowledge-steward
  维护手册"文档导入幂等"约定,幂等仅对未变更内容成立)。
- **错误语义**:单 URL 失败 → IngestError(明确消息:HTTP 状态/超时/无正文),
  CLI 全部失败退出码 1;批量模式单条失败 log+继续,**全部失败才报错**。
- **CLI**:`python -m agents.kingdee_plugin_agent.tools.ingest
  --url <URL> [--url ...] | --dir <目录> | --seed-internal | --delete-source <source>
  --collection api_ref|guide|experience [--title X] [--data-dir <dir>]`

## 2. 灌入结果(2026-08-09 实跑,data/kingdee-rag,gitignored;重跑新增 0)

### guide(71 chunks / 27 源)

内部 skill(51 chunks / 21 源;knowledge-steward 的维护文档因本报告同期编辑,
最终以编辑后内容灌入):

| 源 | chunks |
|---|---|
| code-generator/SKILL.md + references/{bill,list,service}.md | 2+1+1+1 |
| code-reviewer/SKILL.md + references/{bill,list,service}.md | 3+1+1+1 |
| compile-fixer/SKILL.md + references/errors.md | 2+2 |
| design-builder/SKILL.md + references/{bill,list,service}.md | 4+1+1+1 |
| knowledge-steward/SKILL.md + references/{distillation,maintenance}.md | 4+11+10 |
| requirement-clarify/SKILL.md + {bill,list,service}.md | 1+1+1+1 |

金蝶官方(20 chunks / 6 源):

| 源 | 标题 | chunks |
|---|---|---|
| article/57859651290906368 | BOS 平台知识地图 | 3 |
| knowledge/366583980801404672 | 星空 BOS 平台简介 | 1 |
| article/296009613387061504 | 熊说金蝶 BOS 知识库 | 4 |
| article/685345938776315392 | BOS FAQ 精选 | 6 |
| article/862659205751773184 | 收款单扩展实操 | 3 |
| article/851119532046788864 | AI 辅助二开 | 3 |

### api_ref(4 chunks / 3 源,全部官方)

| 源 | 标题 | chunks |
|---|---|---|
| article/758373575135635712 | 星空企业版开发笔记(BusinessDataServiceHelper.Save / DBServiceHelper / FormMetaDataCache 用法) | 2 |
| knowledge/324915652312475136 | WebAPI 多选基础资料 | 1 |
| topics/5 | WebAPI 系统集成主题 | 1 |

9 个官方 URL 全部成功,0 失败。模板类 `templates/*.cs` 未入库(代码模板由 w3 直接使用)。

## 3. 检索冒烟验证(实跑)

| 查询 | 结果 |
|---|---|
| `hybrid_search("guide", "插件开发", k=10)` | 官方知识库页居前,内部 skill 命中在 4-5/7/9-10 位(code-generator / design-builder)✅ 任务要求"返回内部 skill 命中"满足 |
| `hybrid_search("api_ref", "BusinessDataServiceHelper", bm25_weight=0.7)` | 首位命中星空企业版开发笔记(含 BusinessDataServiceHelper.Save)✅ |
| `hybrid_search("guide", "WebAPI 系统集成 集成方式")` | 命中 BOS 知识地图 + 熊说知识库 ✅ |

真实数据验证在报告内完成(不写进自动化测试 —— 测试依赖爬取数据会 flaky/联网,
单元测试全部用 tmp 目录 + mock)。

## 4. 文件变更

- `agents/kingdee_plugin_agent/tools/ingest.py`(新):导入管线 + CLI
- `tests/test_ingest.py`(新):17 项
- `agents/kingdee_plugin_agent/skills/knowledge-steward/SKILL.md`:文档导入步骤
  改引 ingest CLI
- `agents/kingdee_plugin_agent/skills/knowledge-steward/references/maintenance.md`:§2 重写为 ingest 三形态 + 幂等 + plugin_type 缺口注明
- `docs/kingdee-plugin-agent/manual.md`:新增 §1.3 灌入 RAG 知识库(命令 + 已灌清单 + 抽查)
- `docs/kingdee-plugin-agent/project.md`:§5.2 待办"RAG 内容"更新(已接真实资料,剩 standards + plugin_type)
- `CHANGELOG.md`:v1.14.0

## 5. 测试

全套 `pytest tests/ -q`:**237 passed**(212 基线 + 25 新,2 warnings)。新测试覆盖:
围栏跨段落整体保留 / 超长围栏不切分 / 未闭合围栏保留 / 长段落句末切分无内容
丢失、HTML 噪音(script/nav/分享收藏)剔除 + **&lt;pre&gt; 缩进保留**、
ingest_dir tmp 目录入库可检索 + frontmatter 剔除 + 去重幂等、**编辑后重跑重复
→ delete_source 删旧重灌干净**、ingest_url mock HTTP 入库 + HTTP 错误明确消息、
**fetch_html 真实异常映射(超时/HTTP 状态/网络错误 → IngestError)**、
CLI --dir 可运行 / 单 URL 失败退出 1 / 多 URL 部分失败继续 / --delete-source /
无参数退出 2。

## 6. 关注点(concerns)

1. **插件开发检索中官方页居前、内部 skill 靠后**:官方长文(知识地图/FAQ 汇总)
   分块多、BM25 命中多,内部 skill 文档较短。w2/w3 已通过 skill 自身注入链拿到
   SKILL.md,guide 检索是补充信号 —— 当前排序可接受;若需内部优先,可给内部
   source 加权重或 filter(待定)。
2. **plugin_type 元数据缺口**:外部导入文档无 plugin_type,guide 库按
   plugin_type 过滤的检索契约对这批文档会漏召回(已写进 maintenance.md 待办)。
3. **HTML 提取是"够用级"**:stdlib html.parser 对复杂页面(嵌套表格/代码高亮)
   会丢少量结构,但 9 页抽查正文完整、API 名/代码保留;页面改版后建议抽查。
4. **经验库 experience 10 chunks / 1 源(seed)**:本次不动,与设计一致。
5. **官方页创作者信息噪音**:赞赏/浏览计数等动态行已剔(并保证重跑稳定),
   作者名等个别静态残留(如"布鲁biubiu")不影响检索,后续可按需扩充样板模式。

## 7. 修复记录(coordinator review,2026-08-09)

Review 发现 1 Important + 3 Minor,已全部修复:

### 7.1 [Important] "幂等重跑新增 0" 声明不实 —— 去重式而非同步式

- **问题**:去重按 source + 文本;同一 commit 内先灌入后编辑文档(knowledge-steward
  维护文档),重跑即 +10 新旧并存;编辑已灌入文档会静默产生重复。
- **修复**:
  (a) 新增 `delete_source(collection, source)` + CLI `--delete-source <source>`,
      使"删旧重灌"可执行化;
  (b) 全库清空重灌(data/kingdee-rag 已 drop,experience seed 10 + 内部 skill
      51 + 官方 15 全部重灌,以编辑后内容为准),验证**重跑全部 +0**(内部
      --seed-internal、guide 官方页、api_ref 官方页逐一实测);
  (c) 声明修正:CHANGELOG / manual.md / maintenance.md / knowledge-steward
      SKILL.md 全部改为"去重式幂等,内容变更需 --delete-source 删旧重灌";
  (d) 追加测试:`test_edit_rerun_adds_new_chunks_then_delete_source_clean`
      (编辑重跑重复 → 删旧 → 重灌干净)、`test_delete_source_unknown_returns_zero`、
      `test_cli_delete_source`。
- **附带发现**:官方页浏览计数("40次浏览"→"41次浏览")是动态内容,曾致同 URL
  重跑 +1 —— 已将 `N次浏览/N人赞赏了该文章/未经作者许可/原创/所属*:` 纳入
  样板剔除,两次抓取 diff 验证文本稳定,重跑实测 +0。

### 7.2 [Minor] &lt;pre&gt; 代码缩进被折叠(dead code `_in_pre`)

- **问题**:`_in_pre` 标记从未被读取;clean_text 折叠所有行空白,pre 代码
  缩进/结构丢失。
- **修复**:`_HtmlToText.parts` 改为 `(in_pre, text)` 元组流;`_assemble_text`
  按 pre 感知装配 —— pre 行原样保留(缩进/空行),非 pre 行折叠 + 剔样板;
  ingest_url 改用 `html_to_text` 直接输出(不再过 clean_text);clean_text
  保留为纯文本场景工具并注释警示。
- **测试**:`test_html_pre_keeps_indentation_and_structure`(缩进/嵌套保留)、
  `test_clean_text_plain_text`;_FAKE_HTML 的 pre 加缩进,ingest_url 入库断言
  缩进保留。

### 7.3 [Minor] fetch_html 错误映射未测

- **问题**:原测试全部 mock `fetch_html` 本身,真实异常→IngestError 分支无覆盖。
- **修复**:monkeypatch `httpx.get` 直接注入真实异常 ——
  `TimeoutException → "请求超时"`、`HTTPStatusError(404) → "HTTP 404"`、
  `ConnectError → "网络错误"`,三个测试覆盖。

### 7.4 灌入数据最终状态(修复后重灌)

- guide 71 chunks / 27 源(内部 51 + 官方 20);api_ref 4 chunks / 3 源;
  experience 10(seed);全部来源重跑 +0;冒烟检索通过(guide"插件开发"含内部
  skill 命中,api_ref"BusinessDataServiceHelper"首位命中开发笔记)。
