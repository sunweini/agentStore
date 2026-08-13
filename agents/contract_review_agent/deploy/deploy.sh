#!/usr/bin/env bash
# contract-review-agent 生产部署:rsync 上机 → 远端 build → compose up → 健康检查 → 法条向量库 seed。
# 用法:bash agents/contract_review_agent/deploy/deploy.sh
# 前置(见 deploy/README.md):远端 .env 已放置、MySQL 表已建、管理员 apikey 已建。
set -euo pipefail

# 目标机参数(未部署过,先按生产惯例填;可用环境变量覆盖):
#   REMOTE_HOST=... REMOTE_USER=... SSH_PORT=... SSH_KEY=... bash deploy.sh
REMOTE_HOST="${REMOTE_HOST:-10.33.17.72}"
REMOTE_USER="${REMOTE_USER:-root}"
SSH_PORT="${SSH_PORT:-9166}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_loginmonitor}"
REMOTE_DIR="${REMOTE_DIR:-/opt/contract-review-agent}"
# 测试环境覆盖:COMPOSE_FILE=docker-compose.test.yml + PORT=8001(避让线上 sentiment 8000)
COMPOSE_FILE="${COMPOSE_FILE:-}"
PORT="${PORT:-8000}"
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"

SSH_CMD=(ssh -o BatchMode=yes -p "$SSH_PORT" -i "$SSH_KEY" "$REMOTE_USER@$REMOTE_HOST")

# compose 参数:默认生产 compose;COMPOSE_FILE 非空则追加覆盖文件
_COMPOSE_ARGS=(-f agents/contract_review_agent/deploy/docker-compose.yml)
if [ -n "$COMPOSE_FILE" ]; then
  _COMPOSE_ARGS+=(-f "agents/contract_review_agent/deploy/$COMPOSE_FILE")
fi

echo "== 1/6 远端目录 =="
"${SSH_CMD[@]}" "mkdir -p $REMOTE_DIR /home/logs/contract-review-agent"

echo "== 2/6 rsync 代码(排除 .git/.env/根 data/缓存;--delete 不动排除项) =="
rsync -az --delete \
  --exclude '.git' \
  --exclude '.env' \
  --exclude '/data' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.claude' \
  -e "ssh -o BatchMode=yes -p $SSH_PORT -i $SSH_KEY" \
  "$REPO_ROOT/" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/"

echo "== 3/6 校验远端 .env(缺失则中止,手工放置后重跑) =="
if ! "${SSH_CMD[@]}" "test -f $REMOTE_DIR/.env"; then
  echo "!! $REMOTE_DIR/.env 不存在。手工创建(LLM_*/MYSQL_URL/ADMIN_APIKEY/BAIDU_OCR_*/EMBEDDING_*)后重跑本脚本。"
  exit 1
fi

echo "== 4/6 build + up =="
"${SSH_CMD[@]}" "cd $REMOTE_DIR && docker compose ${_COMPOSE_ARGS[*]} up -d --build"

echo "== 5/6 健康检查 =="
sleep 8
"${SSH_CMD[@]}" "curl -sf http://localhost:$PORT/health"
echo "健康检查通过"

echo "== 6/6 法条向量库 seed(空则灌一次;--if-empty 幂等,非空跳过) =="
# 法条向量库(语义检索)依赖远端嵌入服务;deploy 前未 seed 时向量库为空。
# --if-empty 检测空库才灌(seed 非幂等,重复跑会追加向量),非空直接跳过。
# 失败不中断部署:精确校验(_exact,由 laws_dir 构造即加载)不受影响,仅语义
# 检索为空;修复嵌入服务后可重跑本命令。
_SEED="cd $REMOTE_DIR && docker compose ${_COMPOSE_ARGS[*]} exec -T api python -m agents.contract_review_agent.scripts.seed_laws --data-dir /app/data/contract-rag --laws-dir /app/agents/contract_review_agent/data/laws --if-empty"
if "${SSH_CMD[@]}" "$_SEED"; then
  echo "法条向量库已就绪(--if-empty:非空跳过,空则灌内置三法)"
else
  echo "!! seed 未执行/失败(常见:远端嵌入服务 EMBEDDING_* 不可达)。API 已部署、精确校验不受影响,"
  echo "   但法条语义检索为空;修复后重跑:${_SEED}"
fi

echo "部署完成:API http://$REMOTE_HOST:$PORT/health"
