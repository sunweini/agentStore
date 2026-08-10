# sentiment-query-agent 生产部署设计(10.33.17.72)

日期:2026-08-10
状态:设计已确认(方案 A:Docker Compose),实施中

## 1. 背景

测试环境 10.15.80.5 验证通过,部署线上环境 10.33.17.72。用户为内部团队,预期并发几十以内。

## 2. 线上环境探索结论(2026-08-10 实测)

| 项 | 状态 |
|---|---|
| OS | CentOS 7 |
| Docker | 26.0.0 + Compose v2.25.0 |
| SSH | 端口 9166,root + id_loginmonitor 证书登录 |
| 已占端口 | 3306(mysql)、8080(logincenter)、8081/8082(gateway admin/proxy)、9465-9469(MCP 池)、9166(sshd) |
| 可用端口 | **8000(API)、80(nginx)** |
| 日志惯例 | `/home/logs/`(独立 47G 分区) |
| 部署惯例 | `/opt/<项目>/` compose 目录(参考 `/opt/mcp-gateway-cfg/deploy/`) |
| 出网 | DeepSeek API 可达;OpenAI 不通(与 .env 的 LLM_PROVIDER=deepseek 一致) |
| MCP gateway | 本机 8082(`http://10.33.17.72:8082/mcp`,.env 已指向) |
| firewalld | inactive,本地服务器无云安全组 |

## 3. 架构

```
/opt/sentiment-query-agent/              ← rsync 落点(仓库完整副本)
├── agents/sentiment_query_agent/deploy/
│   ├── Dockerfile                       ← build context = 仓库根
│   ├── docker-compose.yml               ← api + nginx
│   ├── nginx.conf
│   ├── deploy.sh                        ← rsync 上机 + 远端 build/up
│   └── README.md
├── .env                                 ← 生产密钥,手工放置,不进 git
├── data/            → 容器 /app/data    ← sqlite checkpoint + JSON 库持久卷
└── (logs)           → /home/logs/sentiment-query-agent/(挂卷)
```

### 3.1 api 服务

- 镜像:python:3.11-slim + requirements.txt
- 启动:`uvicorn agents.sentiment_query_agent.api:app --host 0.0.0.0 --port 8000 --workers 1`
- **单 worker 决策**:流水线 I/O-bound,asyncio 单进程并发足够;多 worker 会破坏 scheme_store index.json(见 §4)
- 端口映射:8000:8000
- `restart: unless-stopped` + healthcheck(`/health` 已存在)
- env_file:`.env`(LLM key、API_KEYS_JSON、MCP_GATEWAY_URL、LOG_DIR)

### 3.2 nginx 服务

- 端口 80,静态托管 `web/demo.html`
- demo.html 的 `API` 常量自动拼 `hostname:8000`,**前端零改动**;CORS 生产收紧为 80 源

### 3.3 持久化

| 容器路径 | 宿主路径 | 内容 |
|---|---|---|
| `/app/data` | `/opt/sentiment-query-agent/data` | checkpoints.sqlite + schemes/ + billing/ |
| `/app/logs` | `/home/logs/sentiment-query-agent` | api.log(RotatingFileHandler,10MB×5) |

日志双通道:文件落盘(LOG_DIR 环境变量开启)+ stdout(docker json-file,限 10MB×3)。

## 4. 并发加固(单 worker 前提,几十并发)

1. **checkpoint sqlite 开 WAL**:流水线写 + 进度轮询读走不同连接,`PRAGMA journal_mode=WAL` 防 `database is locked`(agent.py / api.py)。
2. **scheme_store index.json 加锁**:读-改-写加线程锁 + fcntl 文件锁(对齐 billing.py 现成模式),防多协程/多进程丢索引。
3. 计费防刷已内置:每用户最多 5 pending(billing.py `_MAX_PENDING`)。

真实吞吐上限 = LLM API rate limit + MCP gateway,非 worker 数。未来上百并发需 PostgresSaver + store 进 DB 的无状态化改造(另立项)。

## 5. 部署流程(deploy.sh)

1. 本地 rsync 仓库 → `/opt/sentiment-query-agent/`(排除 .git、__pycache__、.env)
2. 远端:`docker compose -f agents/sentiment_query_agent/deploy/docker-compose.yml up -d --build`
3. `.env` 首次手工放置(含生产 LLM key、正式 apikey),rsync 不覆盖

## 6. 回滚

- 镜像带构建时间 tag,compose 回指旧 tag 重启
- data/ 卷独立于镜像,回滚不丢数据
- 回滚命令:`docker compose down && <改 tag> && docker compose up -d`

## 7. 验收标准

- [ ] `curl http://10.33.17.72:8000/health` = `{"status":"ok"}`
- [ ] `http://10.33.17.72/` 打开 demo.html
- [ ] 全流程跑通:提交 → 6 步进度 → 勾选 → commit → 导出 Excel
- [ ] `/home/logs/sentiment-query-agent/api.log` 有结构化日志
- [ ] 容器重启后方案组数据仍在(data 卷持久化)

## 8. 范围约束

- 代码改动限 `agents/sentiment_query_agent/` 内
- 不改根目录 docker-compose.yml(compose 自包含在 agent deploy/ 目录)
- 设计文档存档 docs/superpowers/specs/(项目惯例)
- 遗留:`.env.example` 含真实 MCP token,待轮换(用户决定暂不处理)
