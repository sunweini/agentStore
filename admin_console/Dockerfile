# admin_console 生产镜像(管理控制台:跨 agent apikey/角色/额度/报表)。
# build context = 仓库根目录(compose 里 context: ..),勿在本目录直接 docker build。
# 生产服务器(10.33.17.72)无法访问 docker.io/pypi.org:
# - 基础镜像用本机已有的 python:3.12-slim(与 sentiment/contract 同惯例)
# - pip 走阿里云源
FROM python:3.12-slim

WORKDIR /app

# 先装依赖,利用层缓存(代码变动不重装)
COPY admin_console/requirements-admin.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r /tmp/requirements.txt

# 代码:共享层(common 的 admin_api/auth/billing/apikey_mgmt/db/config)+ 前端静态页
COPY common/ ./common/
COPY web/ ./web/

EXPOSE 8003

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8003/', timeout=3)" || exit 1

# 单 worker:admin_api 无状态,单 worker 足够。健康检查打 /(serve admin.html,
# 无鉴权恒 200);超管接口本身由 Bearer 鉴权,不用于健康检查。
CMD ["uvicorn", "common.admin_api:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "1"]
