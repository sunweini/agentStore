# admin_console 生产部署

管理控制台:跨 agent apikey 管理 / 角色切换 / 额度 / 报表(summary + 按天 committed 趋势)。
代码在 `common/admin_api.py`(FastAPI app),前端 `web/admin.html`,本目录只放部署资产。

## 架构

- **独立容器** `admin`(镜像 `admin-console:latest`),跑 `uvicorn common.admin_api:app`,端口 8003。
- **网络**:`deploy_mcp-net`(连 MySQL `deploy-mysql-1`)+ `deploy_default`(被 sentiment 的 `deploy-nginx-1` 反代)。
- **访问入口**:nginx 80 端口 `/admin/` 路径 → 反代 admin 容器根。片段 `nginx-admin.conf` 部署时 `docker cp` 注入 sentiment nginx 容器并 reload(不污染 sentiment 目录)。
- **鉴权**:`Authorization: Bearer <ADMIN_APIKEY>`(.env 生产已配 `sk-demo-hefangyuan20260810`)。

## 前置

- 生产 `.env`(与 sentiment 同 `/opt/sentiment-query-agent/.env`)已含:
  - `ADMIN_APIKEY`(超管,额度 99999999 的 admin key,也用于 console 登录)
  - `MYSQL_URL=mysql://mcp:***@deploy-mysql-1:3306/agentstore`
- MySQL `agentstore` 库已建表 `agent_api_keys` / `agent_billing_records`(v1.26.0 生产切换时已建,本次无需迁移)。

## 发布

```bash
bash admin_console/deploy.sh
```

步骤:rsync 上机 → 校验 .env → 远端 build → compose up → 注入 nginx 片段 + reload → 健康检查。

## 回滚

```bash
# 停止容器(镜像保留)
ssh -p 9166 -i ~/.ssh/id_loginmonitor root@10.33.17.72 \
  "cd /opt/sentiment-query-agent && docker compose -f admin_console/docker-compose.yml down"
# 移除 nginx 片段
ssh -p 9166 -i ~/.ssh/id_loginmonitor root@10.33.17.72 \
  "docker exec deploy-nginx-1 rm -f /etc/nginx/conf.d/admin.conf && docker exec deploy-nginx-1 nginx -s reload"
```

## 端口约定

| 服务 | 端口 |
|---|---|
| sentiment api | 8000 |
| contract 生产 | 8002 |
| **admin_console** | **8003** |
| gateway admin | 8081 |
| nginx(80) | `/admin/` → admin |

## 健康检查

容器 HEALTHCHECK 打 `http://localhost:8003/`(serve admin.html,无鉴权恒 200)。
超管接口本身 Bearer 鉴权,不用于健康检查。
