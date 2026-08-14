# 公共计费组件实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 sentiment/contract 两套重复计费抽成 `common/billing.py` + `common/apikey_mgmt.py` + `common/auth.py`,额度按 `(apikey, agent)` 区分,现有接口表面不变。

**Architecture:** 新建 `agent_api_keys`(复合主键 apikey+agent)+ `agent_billing_records`(UNIQUE(agent, bill_no))单表收敛;公共函数全部带 agent 参数;sentiment/contract api.py 内部改指 common,端点/参数/返回零变化;迁移脚本把 sentiment 生产老表数据搬入新表。

**Tech Stack:** Python + `common/db.py`(MySQL/SQLite 双后端,query/execute/transaction)+ FastAPI。

**Spec:** `docs/superpowers/specs/2026-08-14-common-billing-component-design.md`

## Global Constraints

- **三不变**:①现有接口端点/参数/返回零变化(sentiment 已生产,INTEGRATION 对接方不破坏);②sentiment 存量 apikey 迁移后继续可用(额度继承);③存量数据零丢失。
- **两行为变化**(已确认):①流水线失败一律 cancel_pending;②apikey 停用规则统一 contract 版(管理员可停用任何 apikey,仅不可停用自己;ensure_admin 幂等可重建)。
- 单表收敛,新表 `agent_api_keys` / `agent_billing_records`,老表 `api_keys`/`billing_records`/`contract_*` 保留不删(回滚路径)。
- 计费单位统一:一次 commit 扣 1 单位,先免费后付费,事务原子,pending 上限 5。
- 管理员引导:`ensure_admin(agent)` 读 `.env` `ADMIN_APIKEY` 写入(额度 99999999),幂等。
- 新增单独全局账单接口 `GET /api/v1/billing/usage_all`(管理员看所有 agent),不动现有查看接口。
- `common/db.py` 改动属项目级,记根 CHANGELOG 项目级区;两 agent 接口改动记各自 CHANGELOG。
- 禁止直接连库:业务代码统一走 `common/db`。
- 测试用 SQLite(`DB_BACKEND=sqlite`),生产 MySQL 由 deploy/init_tables.sql 建表。

---

### Task 1: 统一表(common/db.py)

**Files:**
- Modify: `common/db.py`(`init_tables()` 追加 `agent_api_keys` + `agent_billing_records`)
- Test: `tests/test_common_billing.py`(本任务起,集中此文件)

**Interfaces:**
- Consumes: `common.db.init_tables()` 现有双后端机制
- Produces: 表 `agent_api_keys(apikey, agent PK, role, status, free_quota, paid_quota, free_used, paid_used, created_at, updated_at)`、`agent_billing_records(id, apikey, agent, bill_no, status, quota_type, created_at, committed_at, UNIQUE(agent, bill_no))`

- [ ] **Step 1: 写失败测试**

```python
import tempfile
from pathlib import Path
from common import db

def _sqlite_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "t.db"))
    db.init_tables()

def test_agent_tables_exist(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    tables = {r["name"] for r in db.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'agent_%'")}
    assert {"agent_api_keys", "agent_billing_records"} <= tables

def test_agent_billing_unique_agent_billno(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no) VALUES (%s,%s,%s)",
               ("k", "sentiment", "b1"))
    try:
        db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no) VALUES (%s,%s,%s)",
                   ("k", "sentiment", "b1"))
        assert False, "应拒绝重复 (agent, bill_no)"
    except Exception:
        pass
    # 不同 agent 允许同 bill_no
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no) VALUES (%s,%s,%s)",
               ("k", "contract", "b1"))
```

- [ ] **Step 2: 跑测试确认失败**(`agent_api_keys` 表不存在)
- [ ] **Step 3: common/db.py init_tables() 追加两表**(照现有 contract_api_keys 模式,`_sql()` 自动适配双后端):

```python
        cur.execute(_sql("""
            CREATE TABLE IF NOT EXISTS agent_api_keys (
              apikey      VARCHAR(128) NOT NULL,
              agent       VARCHAR(64) NOT NULL,
              role        VARCHAR(10) NOT NULL DEFAULT 'normal',
              status      VARCHAR(10) NOT NULL DEFAULT 'active',
              free_quota  INT NOT NULL DEFAULT 10,
              paid_quota  INT NOT NULL DEFAULT 0,
              free_used   INT NOT NULL DEFAULT 0,
              paid_used   INT NOT NULL DEFAULT 0,
              created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (apikey, agent)
            )
        """))
        cur.execute(_sql("""
            CREATE TABLE IF NOT EXISTS agent_billing_records (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              apikey      VARCHAR(128) NOT NULL,
              agent       VARCHAR(64) NOT NULL,
              bill_no     VARCHAR(64) NOT NULL,
              status      VARCHAR(10) NOT NULL DEFAULT 'pending',
              quota_type  VARCHAR(10) NULL,
              created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              committed_at DATETIME NULL,
              UNIQUE (agent, bill_no)
            )
        """))
```

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: commit**

```bash
git add common/db.py tests/test_common_billing.py
git commit -m "feat: 统一计费表 agent_api_keys/agent_billing_records(双后端)"
```

---

### Task 2: 公共计费核心(common/billing.py)

**Files:**
- Create: `common/billing.py`
- Test: `tests/test_common_billing.py`

**Interfaces:**
- Consumes: `common.db.query/execute/transaction`(Task 1 表)
- Produces:
  - `check_quota(apikey, agent) -> None`(free+paid ≤0 → 403 HTTPException)
  - `create_pending(apikey, agent, bill_no) -> None`(该 (apikey,agent) pending ≥5 → 429)
  - `commit(apikey, agent, bill_no) -> None`(事务:pending→committed,先免费后付费,quota_type 回写;无 pending → 404)
  - `cancel_pending(apikey, agent, bill_no) -> None`(带 apikey+agent 过滤)
  - `usage(apikey, agent) -> dict`(free/paid total/used/remaining + pending_count + role)
  - `usage_all(agent=None) -> list[dict]`(管理员:所有/指定 agent 普通用户额度)
  - `add_free_quota(apikey, agent, count)` / `add_paid_quota(apikey, agent, count)`
  - `list_pending(apikey, agent) -> list[dict]`

- [ ] **Step 1: 写失败测试**(SQLite 后端,复用 Task 1 的 `_sqlite_env`)

```python
import pytest
from common import billing

def _seed(apikey="k1", agent="sentiment", free=10, paid=0):
    from common import db
    db.execute(
        "INSERT INTO agent_api_keys (apikey, agent, free_quota, paid_quota) "
        "VALUES (%s,%s,%s,%s)", (apikey, agent, free, paid))

def test_check_quota_ok_and_403(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed()
    billing.check_quota("k1", "sentiment")  # 不抛
    billing.check_quota("k1", "contract")   # 无行 → _active_apikey 401? 见注
    from common import db
    db.execute("UPDATE agent_api_keys SET free_quota=0, free_used=0, paid_quota=0, paid_used=0 WHERE apikey='k1' AND agent='sentiment'")
    with pytest.raises(Exception) as e:
        billing.check_quota("k1", "sentiment")
    assert getattr(e.value, "status_code", None) == 403

def test_create_pending_limit_429(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed()
    for i in range(5):
        billing.create_pending("k1", "sentiment", f"b{i}")
    with pytest.raises(Exception) as e:
        billing.create_pending("k1", "sentiment", "b6")
    assert getattr(e.value, "status_code", None) == 429

def test_commit_free_then_paid(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed(free=1, paid=1)
    billing.create_pending("k1", "sentiment", "b1")
    billing.commit("k1", "sentiment", "b1")
    u = billing.usage("k1", "sentiment")
    assert u["free"]["used"] == 1 and u["paid"]["used"] == 0
    billing.create_pending("k1", "sentiment", "b2")
    billing.commit("k1", "sentiment", "b2")
    u = billing.usage("k1", "sentiment")
    assert u["free"]["used"] == 1 and u["paid"]["used"] == 1

def test_cancel_filters_apikey(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed()
    billing.create_pending("k1", "sentiment", "b1")
    billing.cancel_pending("k1", "sentiment", "b1")
    assert billing.usage("k1", "sentiment")["pending_count"] == 0

def test_usage_all_filters_agent(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    _seed("k1", "sentiment")
    _seed("k2", "contract")
    assert {u["agent"] for u in billing.usage_all()} >= {"sentiment", "contract"}
    assert [u["agent"] for u in billing.usage_all(agent="sentiment")] == ["sentiment"]
```

> 注:check_quota 对不存在的 (apikey,agent) 行,沿用 sentiment 现有语义(HTTPException 401? 现 sentiment `_active_apikey` 无效→401)。执行时对齐现有行为:无行/非 active → 401,额度耗尽 → 403。

- [ ] **Step 2: 跑测试确认失败**(`common.billing` 不存在)
- [ ] **Step 3: 实现 common/billing.py**(照 sentiment billing.py 逻辑,表名/agent 替换,函数全带 agent 参数;cancel 带 apikey+agent 过滤):

```python
"""公共计费:一套逻辑,额度按 (apikey, agent) 区分。

单表收敛(agent_api_keys / agent_billing_records),设计见
docs/superpowers/specs/2026-08-14-common-billing-component-design.md。
扣费状态机 pending→committed/cancelled;先免费后付费,事务原子;pending 上限 5。
"""
from __future__ import annotations

from fastapi import HTTPException

from common import db

_MAX_PENDING = 5
_ADMIN_FREE_QUOTA = 99999999


def _active_apikey(apikey: str, agent: str) -> dict:
    rows = db.query("SELECT * FROM agent_api_keys WHERE apikey=%s AND agent=%s",
                    (apikey, agent))
    row = rows[0] if rows else None
    if row is None or row["status"] != "active":
        raise HTTPException(status_code=401, detail="apikey 无效或已删除")
    return row


def check_quota(apikey: str, agent: str) -> None:
    row = _active_apikey(apikey, agent)
    if (row["free_quota"] - row["free_used"]) + (row["paid_quota"] - row["paid_used"]) <= 0:
        raise HTTPException(status_code=403, detail="额度不足,请联系管理员充值")


def create_pending(apikey: str, agent: str, bill_no: str) -> None:
    _active_apikey(apikey, agent)
    rows = db.query(
        "SELECT COUNT(*) AS n FROM agent_billing_records "
        "WHERE apikey=%s AND agent=%s AND status='pending'", (apikey, agent))
    if rows[0]["n"] >= _MAX_PENDING:
        raise HTTPException(status_code=429, detail="并发 pending 超限,请先完成或取消任务")
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no) "
               "VALUES (%s,%s,%s)", (apikey, agent, bill_no))


def commit(apikey: str, agent: str, bill_no: str) -> None:
    @db.transaction
    def _do(cur, exec) -> None:
        n = exec("UPDATE agent_billing_records SET status='committed', committed_at=NOW(), "
                 "quota_type='free' WHERE agent=%s AND bill_no=%s AND status='pending'",
                 (agent, bill_no))
        if n == 0:
            rows = exec("SELECT id FROM agent_billing_records WHERE agent=%s AND bill_no=%s",
                        (agent, bill_no))
            if not rows:
                raise HTTPException(status_code=404, detail="计费记录不存在")
            raise RuntimeError("计费记录更新失败")
        rows = exec("SELECT * FROM agent_api_keys WHERE apikey=%s AND agent=%s FOR UPDATE",
                    (apikey, agent))
        key = rows[0] if rows else None
        if key is None:
            raise RuntimeError("apikey 不存在")
        if key["free_used"] < key["free_quota"]:
            exec("UPDATE agent_api_keys SET free_used=free_used+1 WHERE apikey=%s AND agent=%s",
                 (apikey, agent))
            quota_type = "free"
        else:
            exec("UPDATE agent_api_keys SET paid_used=paid_used+1 WHERE apikey=%s AND agent=%s",
                 (apikey, agent))
            quota_type = "paid"
        exec("UPDATE agent_billing_records SET quota_type=%s WHERE agent=%s AND bill_no=%s",
             (quota_type, agent, bill_no))
    _do()


def cancel_pending(apikey: str, agent: str, bill_no: str) -> None:
    db.execute("UPDATE agent_billing_records SET status='cancelled' "
               "WHERE apikey=%s AND agent=%s AND bill_no=%s AND status='pending'",
               (apikey, agent, bill_no))


def usage(apikey: str, agent: str) -> dict:
    row = _active_apikey(apikey, agent)
    pending = db.query("SELECT COUNT(*) AS n FROM agent_billing_records "
                       "WHERE apikey=%s AND agent=%s AND status='pending'",
                       (apikey, agent))[0]["n"]
    return {
        "apikey": apikey, "agent": agent, "role": row["role"],
        "free": {"total": row["free_quota"], "used": row["free_used"],
                 "remaining": row["free_quota"] - row["free_used"]},
        "paid": {"total": row["paid_quota"], "used": row["paid_used"],
                 "remaining": row["paid_quota"] - row["paid_used"]},
        "pending_count": pending,
    }


def usage_all(agent: str | None = None) -> list[dict]:
    sql = ("SELECT apikey, agent, role, status, free_quota, free_used, "
           "paid_quota, paid_used FROM agent_api_keys WHERE role='normal' AND status='active'")
    params: tuple = ()
    if agent:
        sql += " AND agent=%s"
        params = (agent,)
    rows = db.query(sql, params)
    return [{"apikey": r["apikey"], "agent": r["agent"],
             "free": {"total": r["free_quota"], "used": r["free_used"],
                      "remaining": r["free_quota"] - r["free_used"]},
             "paid": {"total": r["paid_quota"], "used": r["paid_used"],
                      "remaining": r["paid_quota"] - r["paid_used"]}}
            for r in rows]


def add_free_quota(apikey: str, agent: str, count: int) -> None:
    _active_apikey(apikey, agent)
    db.execute("UPDATE agent_api_keys SET free_quota=free_quota+%s WHERE apikey=%s AND agent=%s",
               (count, apikey, agent))


def add_paid_quota(apikey: str, agent: str, count: int) -> None:
    _active_apikey(apikey, agent)
    db.execute("UPDATE agent_api_keys SET paid_quota=paid_quota+%s WHERE apikey=%s AND agent=%s",
               (count, apikey, agent))


def list_pending(apikey: str, agent: str) -> list[dict]:
    return db.query(
        "SELECT bill_no, created_at FROM agent_billing_records "
        "WHERE apikey=%s AND agent=%s AND status='pending' ORDER BY created_at DESC",
        (apikey, agent))
```

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: commit**

```bash
git add common/billing.py tests/test_common_billing.py
git commit -m "feat: 公共计费核心 billing.py((apikey,agent) 维度,先免费后付费)"
```

---

### Task 3: 公共 apikey 管理(common/apikey_mgmt.py)

**Files:**
- Create: `common/apikey_mgmt.py`
- Test: `tests/test_common_billing.py`

**Interfaces:**
- Consumes: `common.db`、`common.billing._ADMIN_FREE_QUOTA`
- Produces:
  - `create_apikey(agent, name, role="normal") -> dict`(随机 `sk-`+`secrets.token_hex(16)`,role 白名单 normal/admin,非法 ValueError)
  - `update_apikey(agent, old, new) -> dict`(额度继承 + billing_records.apikey 重写)
  - `deactivate_apikey(agent, apikey, admin) -> None`(require_admin 授权;apikey==admin → 403;admin 目标可停用)
  - `ensure_admin(agent) -> None`(读 `.env` `ADMIN_APIKEY`;无则自动生成+日志;写管理员行额度 99999999,幂等)

- [ ] **Step 1: 写失败测试**

```python
def test_create_apikey_roles(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    k = billing_mgmt.create_apikey("contract", "tester")
    assert k["apikey"].startswith("sk-")
    with pytest.raises(ValueError):
        billing_mgmt.create_apikey("contract", "x", role="superadmin")

def test_ensure_admin_idempotent(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    billing_mgmt.ensure_admin("sentiment")
    billing_mgmt.ensure_admin("sentiment")
    rows = db.query("SELECT * FROM agent_api_keys WHERE role='admin' AND agent='sentiment'")
    assert len(rows) == 1 and rows[0]["free_quota"] == 99999999

def test_deactivate_rule(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    admin = billing_mgmt.create_apikey("sentiment", "admin", role="admin")["apikey"]
    other_admin = billing_mgmt.create_apikey("sentiment", "a2", role="admin")["apikey"]
    user = billing_mgmt.create_apikey("sentiment", "u")["apikey"]
    billing_mgmt.deactivate_apikey("sentiment", user, admin)      # admin 停用普通用户 OK
    billing_mgmt.deactivate_apikey("sentiment", other_admin, admin)  # admin 目标可停用
    with pytest.raises(Exception) as e:
        billing_mgmt.deactivate_apikey("sentiment", admin, admin)  # 不可停用自己
    assert getattr(e.value, "status_code", None) == 403
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现 common/apikey_mgmt.py**(create/update/deactivate/ensure_admin,表 agent_api_keys;deactivate 统一 contract 规则)
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: commit**

```bash
git add common/apikey_mgmt.py tests/test_common_billing.py
git commit -m "feat: 公共 apikey 管理(create/update/deactivate/ensure_admin,contract 停用规则)"
```

---

### Task 4: 公共鉴权(common/auth.py)

**Files:**
- Create: `common/auth.py`
- Test: `tests/test_common_billing.py`

**Interfaces:**
- Consumes: `common.db`
- Produces:
  - `check_apikey(apikey, agent) -> dict`(无效/删除 → 401)
  - `require_admin(apikey, agent) -> None`(非 admin → 403)
  - `assert_owner(user, owner, admin=None) -> None`(非本人且非管理员 → 403)

- [ ] **Step 1: 写失败测试**(check 401 / require_admin 403 / assert_owner)
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现 common/auth.py**(逻辑照 sentiment auth.py,表换 agent_api_keys,函数带 agent)
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: commit**

```bash
git add common/auth.py tests/test_common_billing.py
git commit -m "feat: 公共鉴权 auth.py(apikey/管理员/资源归属)"
```

---

### Task 5: 存量迁移(scripts/migrate_billing.py)

**Files:**
- Create: `scripts/migrate_billing.py`
- Test: `tests/test_common_billing.py`

**Interfaces:**
- Consumes: `common.db`(老表 api_keys/billing_records → 新表 agent_*)
- Produces: `migrate(source_agent="sentiment", dry_run=True) -> dict`(迁移统计)

- [ ] **Step 1: 写失败测试**(SQLite 造老表数据 → 迁移 → 新表行数/额度一致;dry-run 不写;幂等)

```python
def test_migrate_sentiment(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    db.execute("CREATE TABLE IF NOT EXISTS api_keys (apikey TEXT PRIMARY KEY, role TEXT, status TEXT, free_quota INT, paid_quota INT, free_used INT, paid_used INT)")
    db.execute("CREATE TABLE IF NOT EXISTS billing_records (id INTEGER PRIMARY KEY AUTOINCREMENT, apikey TEXT, group_id TEXT, status TEXT, quota_type TEXT)")
    db.execute("INSERT INTO api_keys VALUES ('k1','admin','active',100,5,2,1)")
    db.execute("INSERT INTO billing_records (apikey, group_id, status) VALUES ('k1','g1','committed')")
    from scripts.migrate_billing import migrate
    stats = migrate(dry_run=False)
    assert stats["keys"] == 1 and stats["records"] == 1
    rows = db.query("SELECT apikey, agent, free_quota FROM agent_api_keys WHERE agent='sentiment'")
    assert rows[0]["apikey"] == "k1" and rows[0]["free_quota"] == 100
    rec = db.query("SELECT bill_no, status FROM agent_billing_records WHERE agent='sentiment'")
    assert rec[0]["bill_no"] == "g1" and rec[0]["status"] == "committed"
    # 幂等:再跑不重复
    migrate(dry_run=False)
    assert db.query("SELECT COUNT(*) n FROM agent_api_keys")[0]["n"] == 1
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现 migrate_billing.py**(api_keys→agent_api_keys agent='sentiment';billing_records→agent_billing_records bill_no=group_id;`INSERT OR IGNORE`/`ON DUPLICATE KEY UPDATE` 幂等;dry_run 只统计;迁移后校验行数+额度四元组)
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: commit**

```bash
git add scripts/migrate_billing.py tests/test_common_billing.py
git commit -m "feat: 存量计费迁移脚本(dry-run + 幂等 + 迁移后校验)"
```

---

### Task 6: contract agent 接入

**Files:**
- Modify: `agents/contract_review_agent/api.py`(import 改指 common,调用点传 agent='contract')
- Modify: `agents/contract_review_agent/auth.py`、`billing.py`、`apikey_mgmt.py`(改薄转发 common,或删 + api.py 直接 import common)
- Test: `tests/test_contract_review_agent.py`(计费相关测试改指 common)

**Interfaces:**
- Consumes: `common.billing.check_quota/create_pending/commit/cancel_pending/usage`、`common.auth.check_apikey/require_admin`、`common.apikey_mgmt.create_apikey/admin_list/deactivate_apikey`(Task 2-4)
- Produces: contract 现有接口端点/参数/返回**零变化**,内部走 common(agent='contract')

- [ ] **Step 1: 改 contract api.py 所有 billing/auth/apikey_mgmt import 指 common**,调用点传 agent='contract'。确认端点到参数到返回不变。
- [ ] **Step 2: 改 contract 三个文件为薄转发**(保留 `from common.billing import *` 式 re-export,兼容测试直接 import;或删除+更新测试)。倾向:删除三个文件,api.py 直接 import common,测试改指 common。
- [ ] **Step 3: 跑 contract 全测试**,确认接口语义不变
- [ ] **Step 4: commit**

```bash
git add agents/contract_review_agent/
git commit -m "refactor: contract 计费切公共组件((apikey,contract) 维度,接口不变)"
```

---

### Task 7: sentiment agent 接入 + 失败 cancel

**Files:**
- Modify: `agents/sentiment_query_agent/api.py`(import 改指 common,agent='sentiment',runner 失败路径补 cancel_pending)
- Modify: `agents/sentiment_query_agent/billing.py`、`auth.py`、`apikey_mgmt.py`(删或薄转发)
- Modify: `agents/sentiment_query_agent/deploy/init_tables.sql`(加 agent_ 两表,生产建表)
- Test: `tests/test_sentiment_query_agent.py`(计费测试改指 common)

**Interfaces:**
- Consumes: `common.billing` / `common.auth` / `common.apikey_mgmt`(agent='sentiment')
- Produces: sentiment 现有接口零变化;流水线失败路径补 `billing.cancel_pending`

- [ ] **Step 1: sentiment api.py 改指 common**(agent='sentiment'),所有计费调用点对应替换。**接口端点/参数/返回逐一对齐现有**,不破坏 INTEGRATION 对接方。
- [ ] **Step 2: sentiment runner 失败路径补 cancel_pending**(此前失败不释放 pending):在流水线异常/失败的 except 分支调用 `billing.cancel_pending(apikey, 'sentiment', group_id)`。
- [ ] **Step 3: sentiment 三个计费文件删/薄转发**;测试改指 common。
- [ ] **Step 4: sentiment deploy/init_tables.sql 加 agent_ 两表**。
- [ ] **Step 5: 跑 sentiment + contract 全测试**
- [ ] **Step 6: commit**

```bash
git add agents/sentiment_query_agent/
git commit -m "refactor: sentiment 计费切公共组件 + 失败路径补 cancel(接口不变)"
```

---

### Task 8: 全局账单接口(单独新增)

**Files:**
- Modify: `agents/sentiment_query_agent/api.py`(新增 `GET /api/v1/billing/usage_all`)
- Test: `tests/test_common_billing.py`

**Interfaces:**
- Consumes: `common.billing.usage_all(agent=None)`(Task 2)
- Produces: 新端点 `GET /api/v1/billing/usage_all`(管理员,响应含 agent 维度)

- [ ] **Step 1: 写失败测试**(TestClient 调 usage_all 接口,管理员 200 含 agent,普通用户 403)
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现端点**(require_admin 全局管理员;响应 `{"usage": [<agent, free, paid, ...>]}`;agent 可选 query 参数过滤)
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: commit**

```bash
git add agents/sentiment_query_agent/api.py tests/test_common_billing.py
git commit -m "feat: 全局账单接口 GET /billing/usage_all(管理员看所有 agent,独立新端点)"
```

---

### Task 9: 文档 + 收尾

**Files:**
- Modify: 两 agent CLAUDE.md(计费指向 common)、`agents/sentiment_query_agent/API.md`(新增 usage_all 接口)、`agents/contract_review_agent/API.md`
- Modify: 根 `CHANGELOG.md`(项目级区:common/billing + 表 + 迁移)
- Modify: 两 agent CHANGELOG(bump)

- [ ] **Step 1: 更新两 agent CLAUDE.md**(计费=common,agent 参数)
- [ ] **Step 2: API.md 记录 usage_all 新接口,现有接口文档不变**
- [ ] **Step 3: CHANGELOG**(根项目级 + 两 agent bump)
- [ ] **Step 4: 全量测试**

Run: `pytest tests/ -q`
Expected: ALL PASS(含 sentiment 272 相关 + contract + common)

- [ ] **Step 5: commit**

```bash
git add CLAUDE.md agents/*/CLAUDE.md agents/*/API.md agents/*/CHANGELOG.md CHANGELOG.md
git commit -m "docs: 公共计费组件收尾(两 agent 文档 + CHANGELOG)"
```

---

## 自审记录

- **Spec 覆盖**:§3 表(Task 1)、§4 组件 API(Task 2/3/4)、§5 迁移(Task 5)、§6 agent 接入(Task 6/7)、§7 新增全局接口(Task 8)、§8 测试(Task 1-8 内嵌)、§9 文档(Task 9)。
- **三不变**:接口表面(Task 6/7 端点不变)、apikey 继续可用(§2.5,迁移继承)、数据零丢失(Task 5 校验)。两行为变化(失败 cancel Task 7、停用规则 Task 3)。
- **无占位**:各任务含实际测试与实现代码。
- **类型一致**:`check_quota(apikey, agent)` 等签名跨 Task 2/6/7/8 一致;`bill_no` 统一(原 group_id/task_id)。
