# 多用户配额管理与资费统计设计

日期:2026-08-11
状态:已批准(待实现)
分支:`feature/quota-billing-stats`

## 1. 背景与目标

- 现状:apikey → 用户标识映射(`API_KEYS_JSON` 静态配置),计费 JSON 文件(每用户 pending 上限 5),无额度概念、无 apikey 管理、无管理员。
- 目标:**用户即 apikey**。每个 apikey 有免费/付费两种额度,commit 扣减;apikey 可创建/修改/删除;管理员可查全部并调额度;资费查询分普通/管理员两维度。

## 2. 数据模型(MySQL)

**库**:`agentstore`(生产 deploy-mysql-1,不动 mcp_audit)。

**表 `api_keys`**:

```sql
CREATE TABLE api_keys (
  apikey          VARCHAR(128) PRIMARY KEY,
  role            ENUM('admin','normal') NOT NULL DEFAULT 'normal',
  status          ENUM('active','deleted') NOT NULL DEFAULT 'active',
  free_quota      INT NOT NULL DEFAULT 10,    -- 免费额度(初始 10)
  paid_quota      INT NOT NULL DEFAULT 0,     -- 付费额度(充值)
  free_used       INT NOT NULL DEFAULT 0,
  paid_used       INT NOT NULL DEFAULT 0,
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

**表 `billing_records`**:

```sql
CREATE TABLE billing_records (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  apikey      VARCHAR(128) NOT NULL,
  group_id    VARCHAR(64) NOT NULL,
  status      ENUM('pending','committed','cancelled') NOT NULL DEFAULT 'pending',
  quota_type  ENUM('free','paid') NULL,       -- commit 时扣的哪种额度
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  committed_at DATETIME NULL,
  UNIQUE KEY uk_group (group_id)
);
```

**关键语义**:
- apikey 即用户,owner 字段存 apikey 本身
- 删除 = 软删(status='deleted'),数据保留但鉴权拒绝
- 修改 = 换主键,事务内迁移 api_keys + billing_records

## 3. 接口设计(8 个新增)

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| POST | /api/v1/apikeys | 创建 apikey(默认免费 10/付费 0) | 管理员 |
| PUT | /api/v1/apikeys | 修改:旧 key → 新 key(资费继承 + 历史迁移) | 管理员 |
| DELETE | /api/v1/apikeys/{apikey} | 删除(软删) | 管理员 |
| GET | /api/v1/apikeys/pending | 查当前 apikey 的 pending 任务 | 本人 |
| GET | /api/v1/apikeys/list | 查所有普通用户 apikey 额度 | 管理员 |
| GET | /api/v1/billing/usage | 资费查询(普通查自己/管理员查全部) | 本人/管理员 |
| POST | /api/v1/billing/quota/paid | 增加付费额度 | 管理员 |
| POST | /api/v1/billing/quota/free | 增加免费额度 | 管理员 |

**资费查询响应**:

```json
// 普通用户
{"apikey": "sk-x", "free": {"total":10,"used":3,"remaining":7},
 "paid": {"total":0,"used":0,"remaining":0}, "pending_count": 2}

// 管理员
{"users": [{"apikey":"sk-a", "free":{...}, "paid":{...}}, ...],
 "total": {"free_remaining":..., "paid_remaining":...}}
```

## 4. 核心流程

**提交任务**:鉴权(active)→ 额度校验(free+paid remaining > 0,否则 403)→ 并发校验(pending < 5,否则 429)→ 写 pending → 启动流水线。

**确认入库(commit)**:鉴权 + 归属 → 事务:pending→committed 记 quota_type + 额度扣减(**先 free 后 paid**)→ 方案组固化。

**停止任务(stop)**:鉴权 + 归属 → pending→cancelled(释放并发)→ 方案组 stopped。

**额度扣减顺序**:先免费后付费(用户确认)。

**额度不足处理**:仅提交时校验;入库时不校验(已消耗不赖账,用户确认)。

## 5. 鉴权改造(auth.py)

- MySQL 读 api_keys,校验 apikey 存在且 active(否则 401)
- `require_admin(apikey)`:管理接口校验 role='admin'(否则 403)
- 归属:group.owner = apikey(跨 apikey 403,管理员除外)
- `API_KEYS_JSON` 废弃;管理员 key 独立配置 `.env ADMIN_APIKEY`

## 6. 数据迁移(部署时一次性)

- `data/billing/*.json` pending/committed → billing_records
- 现有 apikey → api_keys(管理员 sk-demo-hefangyuan20260810:role=admin,额度 99999999)
- 方案组 owner:用户标识 → apikey

## 7. 配置

- `.env` 新增:`MYSQL_URL=mysql://mcp:***@deploy-mysql-1:3306/agentstore`、`ADMIN_APIKEY=sk-demo-hefangyuan20260810`
- `API_KEYS_JSON` 废弃

## 8. 错误处理

- MySQL 不可用 → 503 明确报错
- 修改冲突(新 key 已存在)→ 409
- 删除管理员 → 403 拒绝
- 并发扣减 → 事务原子,不超扣

## 9. 测试

额度模型/扣减顺序/apikey 管理(创建修改删除)/鉴权(无效/deleted/跨 key/管理员)/资费查询/接口测试(mock 或本地 MySQL)/迁移脚本。

本地测试:SQLite 兼容层或本地 docker MySQL;生产:deploy-mysql-1。

## 10. 实施步骤

1. MySQL 建库建表 + 初始化管理员
2. common/db.py MySQL 连接池
3. billing.py 重写(api_keys + billing_records,事务)
4. auth.py 重写(MySQL 鉴权 + 管理员)
5. api.py 新增 8 接口 + 改造 create/commit/stop
6. 迁移脚本
7. 测试 + 文档 + CHANGELOG
8. **部署生产前等用户确认**(用户明确要求)

## 11. 范围外

前端页面(接口先行)、多档位资费(暂 1 单位/次)、额度过期策略。
