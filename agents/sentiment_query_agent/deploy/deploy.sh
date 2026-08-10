#!/usr/bin/env bash
# sentiment-query-agent 生产部署:rsync 上机 → 远端 build → compose up → 健康检查。
# 用法:bash agents/sentiment_query_agent/deploy/deploy.sh
set -euo pipefail

REMOTE_HOST=10.33.17.72
REMOTE_USER=root
SSH_PORT=9166
SSH_KEY="$HOME/.ssh/id_loginmonitor"
REMOTE_DIR=/opt/sentiment-query-agent
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"

SSH_CMD=(ssh -o BatchMode=yes -p "$SSH_PORT" -i "$SSH_KEY" "$REMOTE_USER@$REMOTE_HOST")

echo "== 1/5 远端目录 =="
"${SSH_CMD[@]}" "mkdir -p $REMOTE_DIR /home/logs/sentiment-query-agent"

echo "== 2/5 rsync 代码(排除 .git/.env/data/缓存;--delete 不动排除项) =="
rsync -az --delete \
  --exclude '.git' \
  --exclude '.env' \
  --exclude 'data' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.claude' \
  -e "ssh -o BatchMode=yes -p $SSH_PORT -i $SSH_KEY" \
  "$REPO_ROOT/" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/"

echo "== 3/5 校验远端 .env(缺失则中止,手工放置后重跑) =="
if ! "${SSH_CMD[@]}" "test -f $REMOTE_DIR/.env"; then
  echo "!! $REMOTE_DIR/.env 不存在。手工创建(LLM_API_KEY/API_KEYS_JSON/MCP_GATEWAY_URL/MCP_GATEWAY_TOKEN/CORS_ORIGINS)后重跑本脚本。"
  exit 1
fi

echo "== 4/5 build + up =="
"${SSH_CMD[@]}" "cd $REMOTE_DIR && docker compose -f agents/sentiment_query_agent/deploy/docker-compose.yml up -d --build"

echo "== 5/5 健康检查 =="
sleep 8
"${SSH_CMD[@]}" "curl -sf http://localhost:8000/health"
echo
echo "部署完成:页面 http://$REMOTE_HOST/ ,API http://$REMOTE_HOST:8000/health"
