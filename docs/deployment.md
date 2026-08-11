# Agent1 部署文档

海外舆情检索方案生成 Agent 的部署与环境配置指南。

## 1. 环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | macOS / Linux(Windows 部分功能受限,见 §6) |
| Python | ≥ 3.11 |
| 网络 | 能访问 gateway MCP(10.33.17.72:8082)+ DeepSeek API + GitHub |

## 2. 依赖与配置

### 2.1 安装依赖

```bash
cd <项目根目录>
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2.2 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`,填写:

| 变量 | 必填 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | ✅ | DeepSeek 密钥(LLM 生成) |
| `MCP_GATEWAY_URL` | ✅ | gateway MCP 地址(默认已填 `http://10.33.17.72:8082/mcp`) |
| `MCP_GATEWAY_TOKEN` | ✅ | gateway MCP 鉴权 token |
| `API_KEYS_JSON` | ✅ | 本服务 API 的 apikey→用户 映射,JSON 格式:`{"sk-key1": "user1"}` |
| `LLM_PROVIDER` / `LLM_MODEL` | — | 供应商/模型(默认 deepseek / deepseek-chat) |
| `OTEL_ENDPOINT` | — | OpenTelemetry collector 地址,配了才上报(留空本地可用) |
| `MYSQL_URL` | ✅(v1.3.0+) | 配额/资费存储,如 `mysql://mcp:pass@deploy-mysql-1:3306/agentstore` |
| `ADMIN_APIKEY` | ✅(v1.3.0+) | 管理员 apikey,额度 99999999,可查全部/调额度 |

### 2.3 运行时数据目录(自动创建,已 gitignore)

```
data/
├── billing/          # 计费记录(v1.3.0 前 JSON;v1.3.0 起迁移 MySQL)
├── schemes/          # 方案组(草稿 .draft.json / 正式 .json / index.json)
└── checkpoints.sqlite  # LangGraph 状态持久化(中断续跑)
```

### 2.4 配额/资费(v1.3.0+)初始化

1. 建表:生产 MySQL 执行 `agents/sentiment_query_agent/deploy/init_tables.sql`
2. 迁移旧数据(先 dry-run 再执行):

```bash
DB_BACKEND=mysql MYSQL_URL=... ADMIN_APIKEY=sk-demo-hefangyuan20260810 \
  python3 agents/sentiment_query_agent/scripts/migrate_legacy.py          # dry-run
DB_BACKEND=mysql MYSQL_URL=... ADMIN_APIKEY=sk-demo-hefangyuan20260810 \
  python3 agents/sentiment_query_agent/scripts/migrate_legacy.py --apply  # 实际执行
```

3. 迁移内容:JSON 计费 → billing_records、方案组 owner 用户标识→apikey、api_keys 初始化(管理员 99999999/普通 10)

## 3. 启动服务

### 3.1 启动 API 服务

```bash
.venv/bin/uvicorn agents.sentiment-query-agent.api:app --host 0.0.0.0 --port 8000
```

- `--host 0.0.0.0` = 内网可访问;仅本机用 `127.0.0.1`
- 生产建议加 `--workers 2+`(多 worker 需确认 SQLite 锁,计费已带 fcntl 文件锁)

### 3.2 启动前端静态页(可选,演示用)

```bash
python3 -m http.server 8080 --directory web --bind 0.0.0.0
```

- 演示页: `http://<host>:8080/demo.html`
- 技术说明书: `http://<host>:8080/tech-doc.html`
- demo.html 的 API 地址自动取访问者的 host,内网其他机器打开会正确指向 API

### 3.3 验证

```bash
curl http://127.0.0.1:8000/health        # → {"status":"ok"}
curl http://<host>:8080/demo.html        # → 200
```

## 4. 使用

见 [docs/api.md](api.md)。核心流程:

```bash
# 1. 提交任务(约 6 分钟后台跑 6 步)
curl -X POST http://<host>:8000/api/v1/groups \
  -H "Authorization: Bearer <apikey>" -H "Content-Type: application/json" \
  -d '{"company_name": "中国十五冶金建设集团有限公司"}'

# 2. 轮询进度,等 status=review
curl http://<host>:8000/api/v1/groups/<group_id>/progress \
  -H "Authorization: Bearer <apikey>"

# 3. 勾选 → 入库 → 导出
```

## 5. 运维

### 5.1 日志

- 服务日志:stdout(FastAPI/uvicorn),结构化 key=value
- 关键事件:`event=pipeline_failed`(流水线失败)、`event=step_error`(单步失败)、`event=mcp_connected`(MCP 连接)

### 5.2 监控(OpenTelemetry)

- 配 `OTEL_ENDPOINT` 后,全链路 trace 上报 collector(API 请求 → 图执行 → 每步 websearch/LLM)
- 未配 collector 不阻塞,本地正常跑

### 5.3 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 提交返回 429 | 该用户未入库方案组 ≥5 个 | 入库或等 24h TTL;清理 `data/billing/<user>.json` |
| progress 404 | 方案组不存在(或 group_id 错) | 确认 group_id;生成中已支持实时读 checkpoint |
| 生成失败 step error | LLM 输出格式错(重试 2 次仍败)/ MCP 全引擎失败 | 看 step_status[].error;重试提交 |
| API 卡死 | 旧版本同步 LLM 阻塞事件循环 | 确认代码已用 `ainvoke`(修复版) |
| MCP 连不上 | gateway 地址/token 错 | 检查 `.env` 的 MCP_GATEWAY_URL/TOKEN |

### 5.4 演示前准备

- 多人演示:每用户先入库再提交下一个(防 429);或演示前清 `data/billing/`
- 生成约 6 分钟,时间紧可提前生成好,现场只演示勾选→入库→导出
- 电脑休眠/关机前先停服务(生成中中断会留在 checkpoint,可续跑)

## 6. 限制

- 计费并发上限:每用户 5 个未入库方案组(pending),`billing.py` 的 `_MAX_PENDING` 可调
- 文件锁 `fcntl` 仅 Unix;Windows 退化到进程内线程锁(单进程部署仍安全)
- 单机部署;多机/多 worker 需换 SQLite → Postgres(checkpointer)并确认文件库共享方案
- 前端演示页 CORS 全放开(`allow_origins=["*"]`),生产按需收紧

## 7. 目录参考

```
agents/sentiment-query-agent/
├── agent.py       # 图入口(run_pipeline)
├── api.py         # FastAPI 接口
├── auth.py        # apikey 鉴权 + 归属校验
├── billing.py     # 计费(并发安全)
├── graph/         # state / nodes(6步) / flows
├── skills/        # overseas-sentiment-query-builder + 分步脚本
├── tools/         # websearch 池(gateway MCP)
└── store/         # 文件库 + 导出转换
```
