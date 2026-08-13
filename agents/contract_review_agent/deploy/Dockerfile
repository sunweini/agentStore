# contract-review-agent 生产镜像。
# build context = 仓库根目录(compose 里 context: ../../..),勿在本目录直接 docker build。
# 生产服务器(参考 10.33.17.72)无法访问 docker.io/pypi.org:
# - 基础镜像用本机已有的 python:3.12-slim(daocloud 源拉取惯例,见机器 docker images)
# - pip 走阿里云源(对齐 /opt/mcp-gateway-cfg/deploy/Dockerfile.base 惯例)
# - 法条向量灌库/检索需要 Chroma,故保留 langchain-chroma(比 sentiment 多这一块);
#   嵌入走远程 EMBEDDING_PROVIDER=openai-compatible(openclaw memorySearch 服务),
#   不含 torch/sentence-transformers(本地 OCR 也不存在:OCR 走百度云端 API)。
FROM python:3.12-slim

WORKDIR /app

# 先装依赖,利用层缓存(代码变动不重装)
COPY agents/contract_review_agent/deploy/requirements-agent.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r /tmp/requirements.txt

# 代码:本 agent + 共享层(common 含 llm/config/rag/db/otel)+ 测试页
COPY common/ ./common/
COPY agents/contract_review_agent/ ./agents/contract_review_agent/
COPY web/ ./web/
COPY pyproject.toml langgraph.json ./

# 持久化挂点:data(contract-rag 法条向量库)、logs(LOG_DIR 落盘)
RUN mkdir -p /app/data /app/logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# 单 worker:_tasks 进程内存 + Chroma 并发模型限制,并发靠 asyncio
CMD ["uvicorn", "agents.contract_review_agent.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
