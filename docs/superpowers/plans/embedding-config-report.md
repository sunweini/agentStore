# RAG embedding 模型配置化 + 切换远程服务重灌 — 实施报告

日期:2026-08-09
Commit:`feat(rag): embedding 模型配置化(EMBEDDING_* env,支持 openai-compatible 远程)`

## 1. 实现

### common/rag.py — `_embedding_model()` 配置化

原实现硬编码 `HuggingFaceEmbeddings(model_name="BAAI/bge-small-zh-v1.5")`(lru_cache 单例)。
改为读 `EMBEDDING_*` 环境变量(经 `common.config.get_env`,与 .env 同源,llm.py 同款约定):

| 变量 | 语义 | 默认 |
|---|---|---|
| `EMBEDDING_PROVIDER` | `huggingface` \| `openai-compatible` | `huggingface` |
| `EMBEDDING_MODEL` | 嵌入模型 | huggingface:`BAAI/bge-small-zh-v1.5`;openai-compatible:`Qwen/Qwen3-Embedding-8B` |
| `EMBEDDING_BASE_URL` | openai-compatible 必填 | 空 → 抛 `RagError` 清晰报错(不静默回退) |
| `EMBEDDING_API_KEY` | 可选,默认空 | 空时传占位符 `not-needed`(langchain-openai 校验要求非空,免鉴权服务不发送真实密钥) |

要点:
- lru_cache(maxsize=1) 单例保留(进程内只构造一次,换 env 需重启生效 —— docstring 注明);
- openai-compatible 分支延迟导入 `langchain_openai.OpenAIEmbeddings`(默认 huggingface 路径不加载该包);
- 依赖确认:.venv 已装 langchain-openai 1.4.1,`OpenAIEmbeddings` 可用(实测构造无网络依赖);
- docstring 明示:**换模型 = 向量空间变更,必须 drop data/kingdee-rag 全量重灌**。

### 测试

- `tests/conftest.py`(新):autouse 夹具清除 `EMBEDDING_*` env + 清 `_embedding_model` 缓存。
  必要性:真实 `.env` 现在配了 openai-compatible,而 common.config 导入时把 .env 写入
  os.environ —— 不清理则 RagClient 测试会真连远程服务(慢、依赖网络)。夹具保证全部测试
  确定性走 huggingface 本地默认。
- `tests/test_rag.py` 新增 5 项:
  1. huggingface 默认模型断言(无 env);
  2. huggingface 自定义 EMBEDDING_MODEL;
  3. openai-compatible 默认模型 + base_url 断言(`Qwen/Qwen3-Embedding-8B` + `openai_api_base`);
  4. openai-compatible 自定义 model + api_key 透传;
  5. openai-compatible 缺 EMBEDDING_BASE_URL → RagError(匹配 "EMBEDDING_BASE_URL")。

## 2. 切换验证(远程服务实测)

- `.env` 追加(本地 gitignored,不入库):
  ```
  EMBEDDING_PROVIDER=openai-compatible
  EMBEDDING_BASE_URL=http://10.33.17.234:32320
  EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B
  ```
- 远程服务实测:`POST /v1/embeddings` 返回 200,单条查询向量 4096 维。

## 3. 维度对比

| 模型 | 维度 | 备注 |
|---|---|---|
| BAAI/bge-small-zh-v1.5(旧,本地) | 512 | sentence-transformers 本地加载 |
| Qwen/Qwen3-Embedding-8B(新,远程 openclaw memorySearch) | **4096** | 实测远程返回;chroma 库内确认 4096 |

## 4. 重灌结果(drop data/kingdee-rag 后全量)

| 集合 | 灌入命令 | chunks(终态) |
|---|---|---|
| experience | `seed_load` | 10(种子,二次重跑 +0) |
| guide | `ingest --seed-internal` + 6 官方 URL | **75** |
| api_ref | 3 官方 URL | **4** |

- guide 75 = 上版 72 + 1(maintenance.md §5 新增段落) + 2(BOS FAQ 精选页源侧漂移);
- 幂等重跑:除 FAQ 活页页(+2,已知源侧漂移,既有结论非管线缺陷)外全部 +0;
- 冒烟 hybrid_search:
  - `guide "插件开发"` → 熊说金蝶BOS知识库居前 + 内部 skill 命中 ✅
  - `api_ref "BusinessDataServiceHelper" bm25_weight=0.7` → 首位星空企业版开发笔记 ✅

## 5. 文件变更

- `common/rag.py`:`_embedding_model()` 配置化 + docstring/模块文档更新
- `.env.example`:EMBEDDING_* 配置组(注释 + 远程示例 + 换模型重灌警告)
- `agents/kingdee_plugin_agent/CLAUDE.md`:常用操作新增「配 embedding 模型」
- `agents/kingdee_plugin_agent/skills/knowledge-steward/references/maintenance.md`:新增 §5 更换 embedding 模型全量重灌流程
- `tests/conftest.py`(新):EMBEDDING_* 测试环境隔离
- `tests/test_rag.py`:5 项 env 分支测试
- `CHANGELOG.md`:v1.15.0
- `.env`(gitignored,不入库):切换 openai-compatible
- `data/kingdee-rag`(gitignored):drop 后全量重灌,4096 维

## 6. 顾虑 / 后续

1. **lru_cache 进程级缓存**:运行时改 .env 需重启进程才生效(单例设计使然,docstring 已注明);
2. **远程服务可用性**:openai-compatible 依赖网络 + openclaw memorySearch 服务在线;服务
   不可用时检索/灌入会报错(与旧本地离线能力不同)。测试不受影响(conftest 隔离);
3. **4096 维存储膨胀**:向量体积为旧 512 维的 8 倍,chroma 持久化目录增长明显,当前
   语料规模(89 chunks)无压力;
4. **活页漂移**:BOS FAQ 精选页源侧漂移 +2 已入库,后续刷新按既有约定 `--delete-source` 重灌;
5. `EMBEDDING_API_KEY` 占位符 `not-needed`:若未来接入需鉴权的服务,配真实 key 即可
   (Bearer 透传已测)。
