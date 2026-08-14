# sentiment-query-agent 生产部署

目标机:10.33.17.72(CentOS 7,Docker 26,SSH 端口 9166)。设计文档:`docs/superpowers/specs/2026-08-10-sentiment-query-agent-prod-deploy-design.md`。

## 文件

| 文件 | 用途 |
|---|---|
| `Dockerfile` | api 镜像(build context = 仓库根,勿在本目录直接 build) |
| `requirements-agent.txt` | 精简依赖(不含 torch/chromadb RAG 栈),版本锁自测试环境 |
| `docker-compose.yml` | api(8000)+ nginx(80),自包含,不动根 compose |
| `nginx.conf` | demo.html 静态托管 + /api 备用反代 |
| `deploy.sh` | rsync 上机 → build → up → 健康检查 |

## 首次部署

1. 远端放 `/opt/sentiment-query-agent/.env`(手工,不进 git/rsync):

   ```
   LLM_PROVIDER=deepseek
   LLM_MODEL=deepseek-chat
   LLM_API_KEY=<生产 key>
   MCP_GATEWAY_URL=http://10.33.17.72:8082/mcp
   MCP_GATEWAY_TOKEN=<token>
   API_KEYS_JSON={"<生产 apikey>": "<用户名>"}   # v1.24.0 起废弃,apikey 存 MySQL
   MYSQL_URL=mysql://mcp:<pass>@deploy-mysql-1:3306/agentstore   # v1.24.0+ 配额/资费
   ADMIN_APIKEY=sk-demo-hefangyuan20260810                       # v1.24.0+ 管理员
   CORS_ORIGINS=http://10.33.17.72
   # OTEL_ENDPOINT 可选
   ```

2. **v1.24.0+ 配额前置**(一次性):
   - 生产 MySQL 建库建表:`mysql -umcp -p<pass> agentstore < agents/sentiment_query_agent/deploy/init_tables.sql`(库 agentstore 需先建,mcp 用户授权)
   - 迁移旧数据:先 dry-run 再执行 `python3 agents/sentiment_query_agent/scripts/migrate_legacy.py`(加 `--apply` 实际写库;需配 MYSQL_URL/ADMIN_APIKEY/API_KEYS_JSON 环境变量)
   - **v1.25.0+ 另需切公共计费(统一表 agent_*)**,见下节「公共计费切换前置」—— 两段迁移都要做(先 migrate_legacy,再 migrate_billing)。

3. 本地仓库根执行:`bash agents/sentiment_query_agent/deploy/deploy.sh`

## 公共计费切换前置(v1.25.0+,重要)

新版本计费/鉴权走统一表 `agent_api_keys` / `agent_billing_records`(agent='sentiment',与 contract 同表同
schema);存量数据在老表 `api_keys` / `billing_records`。**部署新版本前必须先迁移,否则现有 apikey 全 401**:

1. **建表**:`mysql -umcp -p<pass> agentstore < agents/sentiment_query_agent/deploy/init_tables.sql`(已含 agent_* 两表;老表保留不删,幂等重跑无害)。
2. **迁移存量**(先 dry-run 验证,再实迁):
   ```bash
   # 仓库根执行,需 MYSQL_URL 指向生产 agentstore 库
   python3 scripts/migrate_billing.py --dry-run    # 只统计源表行数,不写库
   python3 scripts/migrate_billing.py --apply      # 实迁 + 迁移后校验(行数 + 每 apikey 额度四元组)
   ```
   幂等:已迁入的 (apikey, agent) / (agent, bill_no) 自动跳过,可安全重跑。
3. **部署**:`bash agents/sentiment_query_agent/deploy/deploy.sh`。
4. **回滚**:老表 `api_keys` / `billing_records` 保留不删;需回滚到旧版(v1.24.0,老表存储)时,指定旧镜像 tag 重启即可(见「回滚」节)。

## 端口

- 8000:API(demo.html 直连)
- 80:nginx 演示页

## 日志与数据

- 应用日志:`/home/logs/sentiment-query-agent/api.log`(容器 LOG_DIR=/app/logs,RotatingFileHandler 10MB×5)
- docker 日志:json-file 10MB×3,`docker logs <容器>`
- 数据:`/opt/sentiment-query-agent/data/`(checkpoint sqlite + 方案库 + 计费),容器重启不丢

## 回滚

```bash
# 指定旧镜像 tag 重启(compose 的 IMAGE_TAG 变量)
cd /opt/sentiment-query-agent
IMAGE_TAG=<旧tag> docker compose -f agents/sentiment_query_agent/deploy/docker-compose.yml up -d
```

data/ 卷独立于镜像,回滚不丢数据。

## 注意

- **单 worker 不可改多**:scheme_store index.json / sqlite 并发模型限制(设计文档 §4)
- 基础镜像 `python:3.11-slim` 需从 registry 拉取;若拉不动,配 docker registry mirror 或本地 `docker save/load`
- 整机 rsync 整个仓库(common/ 共享层必需),但只部署本 agent 的 compose
