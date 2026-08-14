-- 多用户配额与资费:建表 SQL
-- 库:agentstore(生产 deploy-mysql-1,已建库并授权 mcp)
-- 用法:mysql -umcp -p<pass> agentstore < init_tables.sql

CREATE TABLE IF NOT EXISTS api_keys (
  apikey          VARCHAR(128) PRIMARY KEY,
  role            ENUM('admin','normal') NOT NULL DEFAULT 'normal',
  status          ENUM('active','deleted') NOT NULL DEFAULT 'active',
  free_quota      INT NOT NULL DEFAULT 10,    -- 免费额度(初始 10)
  paid_quota      INT NOT NULL DEFAULT 0,     -- 付费额度(充值)
  free_used       INT NOT NULL DEFAULT 0,
  paid_used       INT NOT NULL DEFAULT 0,
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS billing_records (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  apikey        VARCHAR(128) NOT NULL,
  group_id      VARCHAR(64) NOT NULL,
  status        ENUM('pending','committed','cancelled') NOT NULL DEFAULT 'pending',
  quota_type    ENUM('free','paid') NULL,     -- commit 时扣的哪种额度
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  committed_at  DATETIME NULL,
  UNIQUE KEY uk_group (group_id),
  KEY idx_apikey (apikey),
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 公共计费统一表(v1.25.0 接入 common 组件后使用,表结构与 common/db.py init_tables 对应):
-- 按 (apikey, agent) 维度收敛,sentiment 走 agent='sentiment'。老表 api_keys/billing_records 保留不删(回滚路径)。
CREATE TABLE IF NOT EXISTS agent_api_keys (
  apikey          VARCHAR(128) NOT NULL,
  agent           VARCHAR(64) NOT NULL,       -- agent 名(sentiment/contract/...)
  role            ENUM('admin','normal') NOT NULL DEFAULT 'normal',
  status          ENUM('active','deleted') NOT NULL DEFAULT 'active',
  free_quota      INT NOT NULL DEFAULT 10,    -- 免费额度(初始 10)
  paid_quota      INT NOT NULL DEFAULT 0,     -- 付费额度(充值)
  free_used       INT NOT NULL DEFAULT 0,
  paid_used       INT NOT NULL DEFAULT 0,
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (apikey, agent)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS agent_billing_records (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  apikey        VARCHAR(128) NOT NULL,
  agent         VARCHAR(64) NOT NULL,         -- agent 名
  bill_no       VARCHAR(64) NOT NULL,         -- 业务单号(sentiment 为 group_id)
  status        ENUM('pending','committed','cancelled') NOT NULL DEFAULT 'pending',
  quota_type    ENUM('free','paid') NULL,     -- commit 时扣的哪种额度
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  committed_at  DATETIME NULL,
  UNIQUE KEY uk_bill (agent, bill_no),
  KEY idx_apikey (apikey),
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
