#!/usr/bin/env bash
# admin_console 生产部署:rsync 上机 → 远端 build → compose up → reload nginx → 健康检查。
# 用法:bash admin_console/deploy.sh
# nginx 路由(/admin/ + /api/v1/admin/)已永久写进 sentiment 的 nginx.conf 源文件,
# rsync 自动带到远端,本脚本只需 reload nginx 让新配置生效,无覆盖 hack。
set -euo pipefail

REMOTE_HOST=10.33.17.72
REMOTE_USER=root
SSH_PORT=9166
SSH_KEY="$HOME/.ssh/id_loginmonitor"
REMOTE_DIR=/opt/sentiment-query-agent   # 与 sentiment 同仓库根(共用 common/ + .env + 网络)
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

SSH_CMD=(ssh -o BatchMode=yes -p "$SSH_PORT" -i "$SSH_KEY" "$REMOTE_USER@$REMOTE_HOST")

echo "== 1/5 远端目录 =="
"${SSH_CMD[@]}" "mkdir -p $REMOTE_DIR"

echo "== 2/5 rsync 代码(排除 .git/.env/data/缓存;不带 --delete,避免删共享目录里 sentiment 的文件) =="
rsync -az \
  --exclude '.git' \
  --exclude '.env' \
  --exclude 'data' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.claude' \
  -e "ssh -o BatchMode=yes -p $SSH_PORT -i $SSH_KEY" \
  "$REPO_ROOT/" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/"

echo "== 3/5 校验远端 .env(需 ADMIN_APIKEY + MYSQL_URL) =="
if ! "${SSH_CMD[@]}" "test -f $REMOTE_DIR/.env"; then
  echo "!! $REMOTE_DIR/.env 不存在。手工创建(ADMIN_APIKEY/MYSQL_URL)后重跑本脚本。"
  exit 1
fi
if ! "${SSH_CMD[@]}" "grep -q '^ADMIN_APIKEY=' $REMOTE_DIR/.env"; then
  echo "!! $REMOTE_DIR/.env 缺 ADMIN_APIKEY。配置后重跑。"
  exit 1
fi

echo "== 4/5 build + up =="
"${SSH_CMD[@]}" "cd $REMOTE_DIR && docker compose -f admin_console/docker-compose.yml up -d --build"

echo "== 5/5 reload nginx(让 rsync 带去的 sentiment nginx.conf 含 admin 路由生效)+ 健康检查 =="
"${SSH_CMD[@]}" "docker exec deploy-nginx-1 nginx -t && docker exec deploy-nginx-1 nginx -s reload"
sleep 6
"${SSH_CMD[@]}" "curl -sf -o /dev/null -w 'admin / → %{http_code}\n' http://localhost:8003/"
"${SSH_CMD[@]}" "curl -sf -o /dev/null -w 'nginx /admin/ → %{http_code}\n' http://localhost/admin/"
echo
echo "部署完成:管理控制台 http://$REMOTE_HOST/admin/ (登录用 .env 的 ADMIN_APIKEY)"
