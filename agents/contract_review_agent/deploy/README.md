# contract-review-agent 生产部署

> **当前状态:部署套件就绪,尚未上机生产**(Task 14 收尾产出;target 机参数见
> `deploy.sh` 顶部,默认参考 10.33.17.72 惯例,可用环境变量覆盖)。

## 文件

| 文件 | 用途 |
|---|---|
| `Dockerfile` | api 镜像(build context = 仓库根,勿在本目录直接 build) |
| `requirements-agent.txt` | 精简依赖(无本地 OCR / 无 torch;含 python-docx/pypdf + Chroma 向量库) |
| `docker-compose.yml` | api(8000),自包含,不动根 compose |
| `init_tables.sql` | MySQL 建 `contract_*` 两表(老,回滚用)+ `agent_api_keys` / `agent_billing_records`(统一表,agent='contract') |
| `deploy.sh` | rsync 上机 → build → up → 健康检查 → 法条向量库 seed(空则灌) |

## 首次部署

1. 远端放 `<部署目录>/.env`(默认 `/opt/contract-review-agent/.env`,手工放置,不进 git/rsync):

   ```
   # LLM(DeepSeek,经 langchain-openai 接入)
   LLM_PROVIDER=deepseek
   LLM_MODEL=deepseek-chat
   DEEPSEEK_API_KEY=<生产 key>

   # 独立计费/鉴权(contract 独立表,与 sentiment 隔离)
   MYSQL_URL=mysql://mcp:<pass>@deploy-mysql-1:3306/agentstore
   ADMIN_APIKEY=sk-<管理员apikey>

   # 百度 OCR(扫描件,无文本层 pdf)
   BAIDU_OCR_API_KEY=<百度OCR API Key>
   BAIDU_OCR_SECRET_KEY=<百度OCR Secret Key>

   # 法条向量嵌入(生产用远程 openai-compatible,避免本地 torch)
   EMBEDDING_PROVIDER=openai-compatible
   EMBEDDING_BASE_URL=http://<embedding-host>:32320
   EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B
   ```

2. **MySQL 建表**(一次性):`mysql -umcp -p<pass> agentstore < agents/contract_review_agent/deploy/init_tables.sql`(库 `agentstore` 需先建,mcp 用户授权;生产若已有该库,幂等重跑无害)。

3. **管理员 apikey**(一次性):建表后调用 `POST /api/v1/apikeys` 需要管理员;首个管理员可直接 SQL 插入统一表 `agent_api_keys`(agent='contract', role='admin'),后续管理员可用管理接口再建。

4. 本地仓库根执行:`bash agents/contract_review_agent/deploy/deploy.sh`(host/port/key 可用 `REMOTE_HOST=/REMOTE_USER=/SSH_PORT=/SSH_KEY=` 环境变量覆盖)。

## 公共计费统一表(v0.8.0,agent_*)

新版本计费/鉴权走统一表 `agent_api_keys` / `agent_billing_records`(agent='contract',与 sentiment 同表同
schema,(apikey, agent) 复合主键,额度按 agent 维度隔离);老表 `contract_api_keys` / `contract_billing_records`
保留不删(回滚路径)。

**生产切换步骤**:

1. **建表**:`mysql -umcp -p<pass> agentstore < agents/contract_review_agent/deploy/init_tables.sql`(已含 agent_* 两表 + 老表,幂等重跑无害)。
2. **存量迁移**:contract **当前无生产存量数据**,`scripts/migrate_billing.py` 无需执行 —— 该脚本仅迁移
   sentiment 老表 api_keys / billing_records;若此前有 contract_* 临时/测试数据需保留,手工迁入 agent_* 即可(当前不适用)。
3. **部署**:`bash agents/contract_review_agent/deploy/deploy.sh`。
4. **回滚**:老表 `contract_*` 保留不删;需回滚到旧版(contract_* 表存储)时,指定旧镜像 tag 重启即可(见「回滚」节)。

> ⚠️ 法条数据分两类,职责分离:
> - **精确索引(校验层)**:内置法条源 `agents/contract_review_agent/data/laws/*.md` 随代码
>   rsync/COPY 进镜像与部署目录(已放行 git),运行时 `LawStore(laws_dir=...)` 构造即加载,**无需 seed**。
> - **向量库(审核节点语义检索)**:`deploy.sh` 第 6 步健康检查后自动灌库 —— 向量库空则
>   `seed_laws --if-empty` 灌内置三法(幂等,非空跳过)。依赖远端 embedding 服务
>   (`EMBEDDING_BASE_URL`)可达;不可达时 deploy 不中断(打警告),校验层仍可精确核验、
>   审核节点法条片段为空降级 suggestion(不崩)。

## 端口

- 8000:API(合同审核 11 接口,鉴权 apikey 头)

## 日志与数据

- 应用日志:`/home/logs/contract-review-agent/api.log`(容器 `LOG_DIR=/app/logs`)
- docker 日志:json-file 10MB×3,`docker logs <容器>`
- 数据:`<部署目录>/data/contract-rag`(法条向量库;compose 把仓库根 `data/` bind-mount 到容器 `/app/data`,重启不丢、容器内可写,deploy 可对其 seed);deploy.sh 第 6 步在向量库空时自动灌;换 embedding 模型后须 drop 该目录重灌(deploy 下次检测到空会自动补)

## 法条向量库 seed

法条分两类数据,语义检索(向量)与精确核验(索引)职责分离:

- **精确索引(校验层)**:`LawStore(laws_dir=...)` 构造即从内置 `data/laws/*.md` 加载,随代码分发,无 seed 依赖。
- **向量库(审核节点语义检索)**:`deploy.sh` 第 6 步健康检查后自动灌库 —— 检测到向量库为空时执行 `seed_laws --if-empty`(幂等:非空跳过),灌内置三法(~294 条)。

可选初始化方案(任选其一,推荐①):

1. **seed(推荐,幂等)**:`deploy.sh` 已内置(第 6 步);也可手动:
   ```bash
   docker compose -f agents/contract_review_agent/deploy/docker-compose.yml exec -T api \
     python -m agents.contract_review_agent.scripts.seed_laws \
     --data-dir /app/data/contract-rag \
     --laws-dir /app/agents/contract_review_agent/data/laws --if-empty
   ```
2. **直接拷贝 dev 的 `data/contract-rag`**:把 dev 机已灌好的向量目录拷贝到生产
   `<部署目录>/data/contract-rag`。**前提**:生产 `EMBEDDING_*` 配置与 dev **完全一致**
   (同模型同 base_url),否则向量空间不匹配、检索无意义,须重灌。

> seed 依赖远端 embedding 服务(`EMBEDDING_BASE_URL`)可达;不可达时 deploy 不中断
> (第 6 步打警告),校验层仍可精确核验,仅语义检索为空,可修复后重跑 seed。

## 回滚

```bash
# 指定旧镜像 tag 重启(compose 的 IMAGE_TAG 变量)
cd /opt/contract-review-agent
IMAGE_TAG=<旧tag> docker compose -f agents/contract_review_agent/deploy/docker-compose.yml up -d
```

data/ 卷独立于镜像,回滚不丢数据。

## 注意

- **单 worker 不可改多**:`_tasks` 进程内存 + Chroma 并发模型限制。
- 基础镜像 `python:3.12-slim` 需从 registry 拉取;若拉不动,配 docker registry mirror 或本地 `docker save/load`。
- 整机 rsync 整个仓库(common/ 共享层必需),但只部署本 agent 的 compose。
- 外部网络 `deploy_default` / `deploy_mcp-net` 需在目标机存在(`deploy_mcp-net` 用于连 MySQL 的 `deploy-mysql-1`;若目标机用其它方式连库,可去掉该网络并改 `MYSQL_URL`)。
- 重启容器会终止运行中的审核任务;发布前先用 `GET /api/v1/contract/status` 确认无在跑任务。
