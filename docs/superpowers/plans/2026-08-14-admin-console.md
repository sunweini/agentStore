# 管理控制台实现计划(apikey 管理 + 报表 + 额度)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 单文件前端管理控制台 + 共享 admin API,跨 agent 管理 apikey/角色/额度 + 状态/历史报表,复用现有计费公共组件,最小改动。

**Architecture:** 新增 `common/admin_api.py`(FastAPI app,超级管理员专用,跨 agent),薄层转调现有 `billing.py`/`apikey_mgmt.py`。公共组件做最小扩展:`auth.is_super_admin`、`apikey_mgmt.set_role/list_keys/list_agents` + `create_apikey` 额度参数、`deactivate_apikey` 超管放行 1 行、`billing.report_summary/report_history`。前端 `web/admin.html` 单文件三 tab。

**Tech Stack:** FastAPI / pydantic v2 / 现有 `common/db.py`(MySQL 生产 / SQLite 测试双后端)/ 原生 JS(无构建/无 CDN)。

**Spec:** `docs/superpowers/specs/2026-08-14-admin-console-design.md`

## Global Constraints

- apikey 是凭据:**所有 key 定向操作 body 传参,key 不进 URL path/query**(防 access log 泄露)。
- console 鉴权:`Authorization: Bearer <ADMIN_APIKEY>`(.env),只对超级管理员开放,不符 403。
- 一 key 一 agent(创建时绑定);操作按 (apikey, agent) 行维度。
- 报表口径:summary 只汇总 `status='active'`;history 只统计 `committed`(按 `date(committed_at)`)。
- 复用现有组件,不改 sentiment api.py / auth 现有函数 / 数据模型 / docker。
- 结构化日志 `service=admin_console component=admin_api`,apikey 日志脱敏用 `_mask_apikey`(OBS-CORE-003)。
- 存储统一走 `common/db.py`,业务代码不直接连库。

---

### Task 1: auth.is_super_admin

**Files:**
- Modify: `common/auth.py`(import + 新函数)
- Test: `tests/test_common_billing.py`(追加)

**Interfaces:**
- Consumes: `common.config.get_env(name, default)` — 已存在。
- Produces: `is_super_admin(token: str) -> bool` — token 等于 `.env ADMIN_APIKEY` 即真;ADMIN_APIKEY 未配(空串)时恒 False。Task 2/5 依赖。

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_common_billing.py` 末尾)

```python
def test_is_super_admin(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ADMIN_APIKEY", "sk-super")
    assert auth.is_super_admin("sk-super") is True
    assert auth.is_super_admin("sk-other") is False
    monkeypatch.delenv("ADMIN_APIKEY", raising=False)
    assert auth.is_super_admin("sk-super") is False  # 未配 → 恒 False(空 token 不匹配)
    assert auth.is_super_admin("") is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_common_billing.py::test_is_super_admin -v`
Expected: FAIL(`AttributeError: module 'common.auth' has no attribute 'is_super_admin'`)

- [ ] **Step 3: 实现**

`common/auth.py` 顶部 import 改为:
```python
from common import config, db
```
文件末尾追加:
```python
def is_super_admin(token: str) -> bool:
    """token 是否为全局超级管理员(.env ADMIN_APIKEY)。未配置 → 恒 False。"""
    return token == config.get_env("ADMIN_APIKEY") and token != ""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_common_billing.py::test_is_super_admin -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add common/auth.py tests/test_common_billing.py
git commit -m "feat: auth.is_super_admin(ADMIN_APIKEY 超管判定)"
```

---

### Task 2: apikey_mgmt 角色管理(deactivate 超管放行 + set_role)

**Files:**
- Modify: `common/apikey_mgmt.py`(import、deactivate_apikey 1 行、set_role 新)
- Test: `tests/test_common_billing.py`(追加)

**Interfaces:**
- Consumes: `auth.is_super_admin(token)`(Task 1)、`auth.require_admin(apikey, agent)`(现有)、`_get_row`/`_ALLOWED_ROLES`(本文件)。
- Produces:
  - `set_role(agent: str, apikey: str, role: str, admin: str) -> dict` — 返回 `{"apikey", "agent", "role"}`。非超管先 `require_admin(admin, agent)`;非法 role 400;不可改自己 403;目标不存在 404。
  - `deactivate_apikey(agent, apikey, admin)` 行为微调:非超管才 `require_admin`(现有行为不变),超管放行。self-guard"不可停用自己"保留。

- [ ] **Step 1: 写失败测试**

```python
def test_set_role(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    user = billing_mgmt.create_apikey("sentiment", "u")["apikey"]
    admin = billing_mgmt.create_apikey("sentiment", "a", role="admin")["apikey"]
    r = billing_mgmt.set_role("sentiment", user, "admin", admin)
    assert r["role"] == "admin"
    assert billing_mgmt._get_row(user, "sentiment")["role"] == "admin"
    with pytest.raises(Exception) as e:
        billing_mgmt.set_role("sentiment", user, "bogus", admin)  # 非法 role → 400
    assert getattr(e.value, "status_code", None) == 400
    with pytest.raises(Exception) as e:
        billing_mgmt.set_role("sentiment", admin, "normal", admin)  # 不可改自己 → 403
    assert getattr(e.value, "status_code", None) == 403
    with pytest.raises(Exception) as e:
        billing_mgmt.set_role("sentiment", "sk-nope", "normal", admin)  # 不存在 → 404
    assert getattr(e.value, "status_code", None) == 404


def test_set_role_normal_user_cannot_promote(tmp_path, monkeypatch):
    """非管理员不能改他人角色(require_admin 前置)。"""
    _sqlite_env(tmp_path, monkeypatch)
    u1 = billing_mgmt.create_apikey("sentiment", "u1")["apikey"]
    u2 = billing_mgmt.create_apikey("sentiment", "u2")["apikey"]
    with pytest.raises(Exception) as e:
        billing_mgmt.set_role("sentiment", u2, "admin", u1)  # u1 非管理员 → 403
    assert getattr(e.value, "status_code", None) == 403


def test_deactivate_super_admin_bypasses_agent_row(tmp_path, monkeypatch):
    """超管放行:新 agent 无 (ADMIN_APIKEY, agent) 行也能停用(M1 修正)。"""
    _sqlite_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ADMIN_APIKEY", "sk-super")
    user = billing_mgmt.create_apikey("brand_new_agent", "u")["apikey"]
    billing_mgmt.deactivate_apikey("brand_new_agent", user, "sk-super")  # 无超管行 → 不再 401
    assert billing_mgmt._get_row(user, "brand_new_agent")["status"] == "deleted"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_common_billing.py::test_set_role tests/test_common_billing.py::test_set_role_normal_user_cannot_promote tests/test_common_billing.py::test_deactivate_super_admin_bypasses_agent_row -v`
Expected: FAIL(`AttributeError: module 'common.apikey_mgmt' has no attribute 'set_role'`)

- [ ] **Step 3: 实现**

`common/apikey_mgmt.py` import 行改为:
```python
from common.auth import is_super_admin, require_admin
```
`deactivate_apikey` 首行鉴权改为(其余不动):
```python
    if not is_super_admin(admin):
        require_admin(admin, agent)
```
文件末尾追加:
```python
def set_role(agent: str, apikey: str, role: str, admin: str) -> dict:
    """改角色(admin↔normal)。非超管先 require_admin;不可改自己;目标不存在 404。"""
    if role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail="非法 role")
    if not is_super_admin(admin):
        require_admin(admin, agent)
    if apikey == admin:
        raise HTTPException(status_code=403, detail="不可修改自己")
    if _get_row(apikey, agent) is None:
        raise HTTPException(status_code=404, detail="apikey 不存在")
    db.execute("UPDATE agent_api_keys SET role=%s WHERE apikey=%s AND agent=%s",
               (role, apikey, agent))
    return {"apikey": apikey, "agent": agent, "role": role}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_common_billing.py -v`
Expected: 全 PASS(含既有 deactivate 测试,超管逻辑不影响普通管理员路径)

- [ ] **Step 5: Commit**

```bash
git add common/apikey_mgmt.py tests/test_common_billing.py
git commit -m "feat: set_role + deactivate 超管放行(跨 agent 超管不依赖 agent 行)"
```

---

### Task 3: apikey_mgmt 创建额度参数 + list_keys + list_agents

**Files:**
- Modify: `common/apikey_mgmt.py`
- Test: `tests/test_common_billing.py`(追加)

**Interfaces:**
- Consumes: `_DEFAULT_FREE_QUOTA`/`_DEFAULT_PAID_QUOTA`/`_ALLOWED_ROLES`(本文件)。
- Produces:
  - `create_apikey(agent, name, role="normal", free_quota=None, paid_quota=None) -> dict` — 额度缺省 None→默认(free 10/paid 0);负额度抛 `ValueError`(与 role 非法同模式);返回含 `free_quota/paid_quota` 实际值。**向后兼容**(旧调用不含新参)。
  - `list_keys(agent: str | None = None) -> list[dict]` — 跨 agent 全量(含 admin/软删),agent 可选过滤;行结构同 `admin_list`(apikey/agent/role/status/free{total,used,remaining}/paid{...})。抽出私有 `_shape_row` 供 `admin_list`/`list_keys` 共用。
  - `list_agents() -> list[dict]` — `[{"agent", "key_count"}]`(仅统计 active key),按 agent 排序。

- [ ] **Step 1: 写失败测试**

```python
def test_create_apikey_custom_quota(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    k = billing_mgmt.create_apikey("sentiment", "x", role="admin", free_quota=50, paid_quota=7)
    assert k["free_quota"] == 50 and k["paid_quota"] == 7
    row = billing_mgmt._get_row(k["apikey"], "sentiment")
    assert row["free_quota"] == 50 and row["paid_quota"] == 7 and row["role"] == "admin"
    # 缺省不变 + 向后兼容
    k2 = billing_mgmt.create_apikey("sentiment", "y")
    assert k2["free_quota"] == 10 and k2["paid_quota"] == 0
    with pytest.raises(ValueError):
        billing_mgmt.create_apikey("sentiment", "z", free_quota=-1)  # 负额度拒绝


def test_list_keys_cross_agent_includes_all(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    admin = billing_mgmt.create_apikey("sentiment", "a1", role="admin")["apikey"]
    u1 = billing_mgmt.create_apikey("sentiment", "u1")["apikey"]
    u2 = billing_mgmt.create_apikey("contract", "u2")["apikey"]
    rows = billing_mgmt.list_keys()
    apis = {(r["agent"], r["apikey"], r["role"]) for r in rows}
    assert ("sentiment", u1, "normal") in apis
    assert ("sentiment", admin, "admin") in apis   # 含 admin
    assert ("contract", u2, "normal") in apis      # 跨 agent
    assert all(r["agent"] == "sentiment" for r in billing_mgmt.list_keys(agent="sentiment"))
    billing_mgmt.deactivate_apikey("sentiment", u1, admin)
    assert any(r["apikey"] == u1 and r["status"] == "deleted" for r in billing_mgmt.list_keys())  # 软删也列出


def test_list_agents_counts_active(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    billing_mgmt.create_apikey("sentiment", "u1")
    billing_mgmt.create_apikey("sentiment", "u2")
    billing_mgmt.create_apikey("contract", "u3")
    agents = {a["agent"]: a["key_count"] for a in billing_mgmt.list_agents()}
    assert agents == {"sentiment": 2, "contract": 1}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_common_billing.py::test_create_apikey_custom_quota tests/test_common_billing.py::test_list_agents_counts_active -v`
Expected: FAIL(create_apikey 签名不兼容 / list_agents 未定义)

- [ ] **Step 3: 实现**

`common/apikey_mgmt.py` 的 `create_apikey` 改为:
```python
def create_apikey(agent: str, name: str, role: str = "normal",
                  free_quota: int | None = None, paid_quota: int | None = None) -> dict:
    """创建 apikey,默认免费 10 / 付费 0;可选初始额度(负值 ValueError)。"""
    if role not in _ALLOWED_ROLES:
        raise ValueError(f"非法 role: {role}(仅允许 {'/'.join(_ALLOWED_ROLES)})")
    fq = _DEFAULT_FREE_QUOTA if free_quota is None else free_quota
    pq = _DEFAULT_PAID_QUOTA if paid_quota is None else paid_quota
    if fq < 0 or pq < 0:
        raise ValueError(f"额度不能为负: free={fq}, paid={pq}")
    for _ in range(3):
        apikey = _gen_apikey()
        try:
            db.execute(
                "INSERT INTO agent_api_keys (apikey, agent, role, status, free_quota, paid_quota) "
                "VALUES (%s, %s, %s, 'active', %s, %s)",
                (apikey, agent, role, fq, pq),
            )
            break
        except RuntimeError as exc:
            if "Duplicate" in str(exc) or "1062" in str(exc) or "UNIQUE" in str(exc):
                continue
            raise
    else:
        raise RuntimeError("apikey 生成冲突:3 次重试仍撞唯一键")
    return {"apikey": apikey, "name": name, "role": role,
            "free_quota": fq, "paid_quota": pq}
```
`admin_list` 行结构抽成私有 helper,`admin_list`/`list_keys` 共用:
```python
def _shape_row(r: dict) -> dict:
    return {
        "apikey": r["apikey"], "agent": r["agent"], "role": r["role"], "status": r["status"],
        "free": {"total": r["free_quota"], "used": r["free_used"],
                 "remaining": r["free_quota"] - r["free_used"]},
        "paid": {"total": r["paid_quota"], "used": r["paid_used"],
                 "remaining": r["paid_quota"] - r["paid_used"]},
    }
```
`admin_list` 的 return 改 `return [_shape_row(r) for r in rows]`。文件末尾追加:
```python
def list_keys(agent: str | None = None) -> list[dict]:
    """跨 agent 全量 apikey(含 admin/软删)。调用方须自行鉴权(console 超级管理员)。"""
    sql = ("SELECT apikey, agent, role, status, free_quota, free_used, "
           "paid_quota, paid_used FROM agent_api_keys")
    params: tuple = ()
    if agent:
        sql += " WHERE agent=%s"
        params = (agent,)
    sql += " ORDER BY agent, apikey"
    return [_shape_row(r) for r in db.query(sql, params)]


def list_agents() -> list[dict]:
    """agent 下拉列表(附 active key 数),按 agent 排序。"""
    rows = db.query(
        "SELECT agent, COUNT(*) AS key_count FROM agent_api_keys "
        "WHERE status='active' GROUP BY agent ORDER BY agent")
    return [{"agent": r["agent"], "key_count": r["key_count"]} for r in rows]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_common_billing.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add common/apikey_mgmt.py tests/test_common_billing.py
git commit -m "feat: create_apikey 额度参数 + list_keys/list_agents 跨 agent 查询"
```

---

### Task 4: billing report_summary + report_history

**Files:**
- Modify: `common/billing.py`
- Test: `tests/test_common_billing.py`(追加)

**Interfaces:**
- Consumes: `db.query`、`agent_api_keys`/`agent_billing_records` 表。
- Produces:
  - `report_summary(agent: str | None = None) -> dict` — `{"agents": [{agent, key_count, free_used, free_remaining, paid_used, paid_remaining}], "total": {同上 5 键}}`。**仅 active key**;agent 可选过滤。
  - `report_history(agent: str | None = None, apikey: str | None = None, days: int = 30) -> dict` — `{"series": [{date, agent, committed}]}`。**仅 committed**,按 `date(committed_at)` 分组,cutoff = `datetime.now() - timedelta(days)`(Python 侧算,避免 INTERVAL 双后端差异)。

- [ ] **Step 1: 写失败测试**

```python
def test_report_summary_active_only(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    db.execute("INSERT INTO agent_api_keys (apikey, agent, free_quota, free_used, paid_quota, paid_used) "
               "VALUES (%s,%s,%s,%s,%s,%s)", ("k1", "sentiment", 10, 3, 5, 1))
    db.execute("INSERT INTO agent_api_keys (apikey, agent, status, free_quota, free_used) "
               "VALUES (%s,%s,%s,%s,%s)", ("k2", "sentiment", "deleted", 10, 9))  # 软删不入汇总
    db.execute("INSERT INTO agent_api_keys (apikey, agent, free_quota, free_used) "
               "VALUES (%s,%s,%s,%s)", ("k3", "contract", 10, 1))
    s = billing.report_summary()
    by_agent = {a["agent"]: a for a in s["agents"]}
    assert by_agent["sentiment"]["key_count"] == 1  # k2 软删不计数
    assert by_agent["sentiment"]["free_used"] == 3
    assert by_agent["sentiment"]["free_remaining"] == 7
    assert by_agent["sentiment"]["paid_used"] == 1 and by_agent["sentiment"]["paid_remaining"] == 4
    assert s["total"]["key_count"] == 2
    assert s["total"]["free_used"] == 4 and s["total"]["paid_remaining"] == 4


def test_report_history_committed_by_day(tmp_path, monkeypatch):
    _sqlite_env(tmp_path, monkeypatch)
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no, status, committed_at) "
               "VALUES (%s,%s,%s,%s,%s)", ("k1", "sentiment", "b1", "committed", "2026-08-01 09:00:00"))
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no, status, committed_at) "
               "VALUES (%s,%s,%s,%s,%s)", ("k1", "sentiment", "b2", "committed", "2026-08-01 10:00:00"))
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no, status, committed_at) "
               "VALUES (%s,%s,%s,%s,%s)", ("k1", "contract", "b3", "committed", "2026-08-01 11:00:00"))
    db.execute("INSERT INTO agent_billing_records (apikey, agent, bill_no, status) "
               "VALUES (%s,%s,%s,%s)", ("k1", "sentiment", "b4", "pending"))  # 非 committed 不入
    h = billing.report_history(days=30)
    series = {(s["date"], s["agent"]): s["committed"] for s in h["series"]}
    assert series[("2026-08-01", "sentiment")] == 2
    assert series[("2026-08-01", "contract")] == 1
    # agent 过滤
    h2 = billing.report_history(agent="sentiment", days=30)
    assert all(s["agent"] == "sentiment" for s in h2["series"])
    assert len(h2["series"]) == 1
    # days=1(今天)过滤掉历史
    h3 = billing.report_history(days=1)
    assert all(s["date"] != "2026-08-01" for s in h3["series"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_common_billing.py::test_report_summary_active_only tests/test_common_billing.py::test_report_history_committed_by_day -v`
Expected: FAIL(`AttributeError: module 'common.billing' has no attribute 'report_summary'`)

- [ ] **Step 3: 实现**

`common/billing.py` 文件末尾追加:
```python
def report_summary(agent: str | None = None) -> dict:
    """按 agent 汇总额度使用(仅 active key)。"""
    sql = ("SELECT agent, COUNT(*) AS key_count, SUM(free_used) AS free_used, "
           "SUM(free_quota - free_used) AS free_remaining, "
           "SUM(paid_used) AS paid_used, SUM(paid_quota - paid_used) AS paid_remaining "
           "FROM agent_api_keys WHERE status='active'")
    params: tuple = ()
    if agent:
        sql += " AND agent=%s"
        params = (agent,)
    sql += " GROUP BY agent ORDER BY agent"
    rows = db.query(sql, params)
    keys = ("key_count", "free_used", "free_remaining", "paid_used", "paid_remaining")
    return {
        "agents": rows,
        "total": {k: sum(r[k] for r in rows) for k in keys},
    }


def report_history(agent: str | None = None, apikey: str | None = None,
                   days: int = 30) -> dict:
    """按天 committed 扣费趋势。cutoff 用 Python datetime 算(规避 INTERVAL 双后端差异)。"""
    from datetime import datetime, timedelta

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    sql = ("SELECT DATE(committed_at) AS d, agent, COUNT(*) AS committed "
           "FROM agent_billing_records WHERE status='committed' AND committed_at >= %s")
    params: list[str] = [cutoff]
    if agent:
        sql += " AND agent=%s"
        params.append(agent)
    if apikey:
        sql += " AND apikey=%s"
        params.append(apikey)
    sql += " GROUP BY DATE(committed_at), agent ORDER BY d, agent"
    rows = db.query(sql, tuple(params))
    return {"series": [
        {"date": r["d"], "agent": r["agent"], "committed": r["committed"]} for r in rows
    ]}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_common_billing.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add common/billing.py tests/test_common_billing.py
git commit -m "feat: billing report_summary(仅 active) + report_history(committed 按天)"
```

---

### Task 5: common/admin_api.py(FastAPI app + 9 接口 + 静态页)

**Files:**
- Create: `common/admin_api.py`
- Test: `tests/test_admin_api.py`(新建)

**Interfaces:**
- Consumes: `auth.is_super_admin`(Task 1)、`apikey_mgmt.create_apikey/set_role/update_apikey/deactivate_apikey/list_keys/list_agents`(Task 2/3)、`billing.add_free_quota/add_paid_quota/report_summary/report_history`(Task 4)。
- Produces: FastAPI `app`,`uvicorn common.admin_api:app` 起服务,`/` 返回 `web/admin.html`,`/api/v1/admin/*` 9 接口。Task 6 前端依赖。

- [ ] **Step 1: 写失败测试** `tests/test_admin_api.py`

```python
"""管理控制台 API 测试(超级管理员,SQLite 后端)。"""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common import db
from common.admin_api import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("ADMIN_APIKEY", "sk-super")
    db.init_tables()
    return TestClient(app)


def _auth():
    return {"Authorization": "Bearer sk-super"}


def test_auth_required(client):
    r = client.get("/api/v1/admin/agents")
    assert r.status_code == 403
    r = client.get("/api/v1/admin/agents", headers=_auth())
    assert r.status_code == 200


def test_create_and_list_key(client):
    r = client.post("/api/v1/admin/apikeys", json={
        "agent": "sentiment", "role": "admin", "free_quota": 50, "paid_quota": 7}, headers=_auth())
    assert r.status_code == 200
    apikey = r.json()["apikey"]
    assert apikey.startswith("sk-")
    r = client.get("/api/v1/admin/apikeys", headers=_auth())
    row = next(x for x in r.json()["keys"] if x["apikey"] == apikey)
    assert row["role"] == "admin" and row["free"]["total"] == 50 and row["paid"]["total"] == 7
    # 负额度 → 400
    r = client.post("/api/v1/admin/apikeys", json={"agent": "sentiment", "free_quota": -1},
                    headers=_auth())
    assert r.status_code == 400


def test_set_role_and_update_and_delete(client):
    k = client.post("/api/v1/admin/apikeys", json={"agent": "sentiment"}, headers=_auth()).json()["apikey"]
    # 改角色
    r = client.patch("/api/v1/admin/apikeys", json={"apikey": k, "agent": "sentiment", "role": "admin"},
                     headers=_auth())
    assert r.status_code == 200 and r.json()["role"] == "admin"
    # 换 key
    r = client.put("/api/v1/admin/apikeys", json={"apikey": k, "agent": "sentiment", "new_apikey": "sk-new012"},
                   headers=_auth())
    assert r.status_code == 200 and r.json()["new_apikey"] == "sk-new012"
    # 软删
    r = client.delete("/api/v1/admin/apikeys", json={"apikey": "sk-new012", "agent": "sentiment"},
                      headers=_auth())
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert client.get("/api/v1/admin/apikeys", headers=_auth()).json()["keys"][0]["status"] == "deleted"


def test_quota_add(client):
    k = client.post("/api/v1/admin/apikeys", json={"agent": "sentiment"}, headers=_auth()).json()["apikey"]
    r = client.post("/api/v1/admin/apikeys/quota", json={"apikey": k, "agent": "sentiment",
                                                         "type": "paid", "count": 10}, headers=_auth())
    assert r.status_code == 200
    row = next(x for x in client.get("/api/v1/admin/apikeys", headers=_auth()).json()["keys"]
               if x["apikey"] == k)
    assert row["paid"]["total"] == 10
    r = client.post("/api/v1/admin/apikeys/quota", json={"apikey": k, "agent": "sentiment",
                                                         "type": "free", "count": 0}, headers=_auth())
    assert r.status_code == 400  # count 必须为正


def test_report_endpoints(client):
    client.post("/api/v1/admin/apikeys", json={"agent": "sentiment", "free_quota": 5}, headers=_auth())
    r = client.get("/api/v1/admin/report/summary", headers=_auth())
    assert r.status_code == 200 and r.json()["total"]["key_count"] == 1
    r = client.get("/api/v1/admin/report/history", headers=_auth())
    assert r.status_code == 200 and isinstance(r.json()["series"], list)
    r = client.get("/api/v1/admin/report/history?days=0", headers=_auth())
    assert r.status_code == 400  # days 越界
    r = client.get("/api/v1/admin/agents", headers=_auth())
    assert r.status_code == 200 and any(a["agent"] == "sentiment" for a in r.json()["agents"])


def test_index_serves_admin_html(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_admin_api.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'common.admin_api'`)

- [ ] **Step 3: 实现** `common/admin_api.py`

```python
"""管理控制台 API:跨 agent apikey 管理 + 报表 + 额度。超级管理员(ADMIN_APIKEY)专用。

设计见 docs/superpowers/specs/2026-08-14-admin-console-design.md。
薄层转调 common/apikey_mgmt.py / common/billing.py;所有 key 定向操作 body 传参
(key 不进 URL,防 access log 泄露凭据)。启动:`uvicorn common.admin_api:app`。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from common import apikey_mgmt, auth, billing

logger = logging.getLogger(__name__)

app = FastAPI(title="agentStore 管理控制台", version="0.1.0")

_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins,
                   allow_methods=["*"], allow_headers=["*"])

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _require_super_admin(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    if not auth.is_super_admin(token):
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    return token


class CreateApiKeyRequest(BaseModel):
    agent: str
    role: str = "normal"
    free_quota: int | None = None
    paid_quota: int | None = None


class SetRoleRequest(BaseModel):
    apikey: str
    agent: str
    role: str


class UpdateApiKeyRequest(BaseModel):
    apikey: str
    agent: str
    new_apikey: str


class DeleteApiKeyRequest(BaseModel):
    apikey: str
    agent: str


class QuotaRequest(BaseModel):
    apikey: str
    agent: str
    type: str
    count: int


@app.get("/api/v1/admin/agents")
async def list_agents_api(admin: str = Depends(_require_super_admin)):
    return {"agents": apikey_mgmt.list_agents()}


@app.get("/api/v1/admin/apikeys")
async def list_keys_api(agent: str | None = None, admin: str = Depends(_require_super_admin)):
    return {"keys": apikey_mgmt.list_keys(agent=agent)}


@app.post("/api/v1/admin/apikeys")
async def create_apikey_api(req: CreateApiKeyRequest, admin: str = Depends(_require_super_admin)):
    try:
        return apikey_mgmt.create_apikey(
            req.agent, "", role=req.role, free_quota=req.free_quota, paid_quota=req.paid_quota)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/v1/admin/apikeys")
async def set_role_api(req: SetRoleRequest, admin: str = Depends(_require_super_admin)):
    return apikey_mgmt.set_role(req.agent, req.apikey, req.role, admin)


@app.put("/api/v1/admin/apikeys")
async def update_apikey_api(req: UpdateApiKeyRequest, admin: str = Depends(_require_super_admin)):
    return apikey_mgmt.update_apikey(req.agent, req.apikey, req.new_apikey)


@app.delete("/api/v1/admin/apikeys")
async def delete_apikey_api(req: DeleteApiKeyRequest, admin: str = Depends(_require_super_admin)):
    apikey_mgmt.deactivate_apikey(req.agent, req.apikey, admin)
    return {"apikey": req.apikey, "agent": req.agent, "deleted": True}


@app.post("/api/v1/admin/apikeys/quota")
async def add_quota_api(req: QuotaRequest, admin: str = Depends(_require_super_admin)):
    if req.count <= 0:
        raise HTTPException(status_code=400, detail="count 必须为正数")
    if req.type == "free":
        billing.add_free_quota(req.apikey, req.agent, req.count)
    elif req.type == "paid":
        billing.add_paid_quota(req.apikey, req.agent, req.count)
    else:
        raise HTTPException(status_code=400, detail="type 必须为 free 或 paid")
    return {"apikey": req.apikey, "agent": req.agent, "type": req.type, "added": req.count}


@app.get("/api/v1/admin/report/summary")
async def report_summary_api(agent: str | None = None, admin: str = Depends(_require_super_admin)):
    return billing.report_summary(agent=agent)


@app.get("/api/v1/admin/report/history")
async def report_history_api(agent: str | None = None, days: int = 30,
                             admin: str = Depends(_require_super_admin)):
    if not 1 <= days <= 365:
        raise HTTPException(status_code=400, detail="days 必须在 1-365")
    return billing.report_history(agent=agent, days=days)


@app.get("/")
async def index():
    return FileResponse(_WEB_DIR / "admin.html")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_admin_api.py -v`
Expected: 全 PASS(若报 `AdminApiKey`/导入问题,按报错修)

- [ ] **Step 5: 全量回归**

Run: `pytest tests/test_common_billing.py tests/test_admin_api.py -v`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add common/admin_api.py tests/test_admin_api.py
git commit -m "feat: 管理控制台 API(跨 agent apikey/角色/额度/报表,超级管理员专用)"
```

---

### Task 6: web/admin.html(三 tab 前端)

**Files:**
- Create: `web/admin.html`

**Interfaces:**
- Consumes: `/api/v1/admin/*` 9 接口(Task 5)。接口契约见 Task 5 测试。
- Produces: 单文件管理页面,`/` 由 admin_api 提供。

前端为静态文件,不做 TDD;验收 = 起服务 curl 接口 + 浏览器实测(webapp-testing)。

- [ ] **Step 1: 写 `web/admin.html`**

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>agentStore 管理控制台</title>
<style>
  :root{--line:#d8d8d8;--bg:#f5f6f8;--fg:#222;--accent:#2563eb;--ok:#16a34a;--warn:#d97706;--bad:#dc2626}
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--fg)}
  header{display:flex;align-items:center;gap:12px;padding:12px 20px;background:#fff;border-bottom:1px solid var(--line)}
  header h1{font-size:17px;margin:0;flex:1}
  header input{width:220px;padding:6px 8px;border:1px solid var(--line);border-radius:4px;font-family:monospace}
  .btn{padding:6px 12px;border:1px solid var(--line);border-radius:4px;background:#fff;cursor:pointer;font-size:13px}
  .btn:hover{background:#eef2ff}.btn.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
  .btn.danger{color:var(--bad);border-color:var(--bad)}
  nav{display:flex;gap:4px;padding:10px 20px;background:#fff;border-bottom:1px solid var(--line)}
  nav button{border:none;background:none;padding:8px 16px;cursor:pointer;font-size:14px;border-bottom:2px solid transparent}
  nav button.active{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}
  main{padding:20px;max-width:1200px;margin:0 auto}
  .tab{display:none}.tab.active{display:block}
  table{width:100%;border-collapse:collapse;background:#fff;font-size:13px}
  th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
  th{background:#fafafa;font-weight:600}
  .badge{padding:2px 8px;border-radius:10px;font-size:12px}
  .badge.admin{background:#fef3c7;color:#92400e}.badge.normal{background:#e0f2fe;color:#075985}
  .badge.active{background:#dcfce7;color:#166534}.badge.deleted{background:#fee2e2;color:#991b1b}
  .low{color:var(--bad);font-weight:600}
  .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;margin-bottom:16px}
  .card{background:#fff;border:1px solid var(--line);border-radius:8px;padding:12px}
  .card h3{margin:0 0 6px;font-size:14px}.card .num{font-size:22px;font-weight:700}
  #chartWrap{background:#fff;border:1px solid var(--line);border-radius:8px;padding:16px;margin-top:16px}
  .modal-mask{position:fixed;inset:0;background:rgba(0,0,0,.4);display:none;align-items:center;justify-content:center}
  .modal-mask.show{display:flex}
  .modal{background:#fff;border-radius:8px;padding:20px;width:420px;max-width:90vw}
  .modal h3{margin:0 0 12px}.modal label{display:block;font-size:13px;margin:10px 0 4px}
  .modal input,.modal select{width:100%;padding:6px 8px;border:1px solid var(--line);border-radius:4px}
  .modal .row{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}
  #msg{font-size:13px;margin-left:8px}
</style>
</head>
<body>
<header>
  <h1>agentStore 管理控制台</h1>
  <input id="adminKey" placeholder="管理员 apikey (ADMIN_APIKEY)" type="password">
  <button class="btn primary" onclick="login()">登录</button>
  <span id="msg"></span>
</header>
<nav>
  <button class="active" onclick="showTab('apikey',this)">apikey 管理</button>
  <button onclick="showTab('report',this)">报表</button>
  <button onclick="showTab('quota',this)">额度管理</button>
</nav>
<main>
  <div id="tab-apikey" class="tab active">
    <div style="margin-bottom:12px"><button class="btn primary" onclick="openCreate()">+ 创建 apikey</button></div>
    <table id="keyTable"><thead><tr>
      <th>agent</th><th>apikey</th><th>角色</th><th>状态</th>
      <th>免费额度(已用/剩余)</th><th>付费额度(已用/剩余)</th><th>操作</th>
    </tr></thead><tbody></tbody></table>
  </div>

  <div id="tab-report" class="tab">
    <div style="margin-bottom:12px">
      <label>agent:<select id="repAgent"><option value="">全部</option></select></label>
      <label>近 <input id="repDays" type="number" value="30" min="1" max="365" style="width:70px"> 天</label>
      <button class="btn primary" onclick="loadReport()">刷新</button>
    </div>
    <div class="cards" id="summaryCards"></div>
    <div id="chartWrap"><h3>按天 committed 趋势</h3><canvas id="histChart" width="1000" height="260"></canvas></div>
  </div>

  <div id="tab-quota" class="tab">
    <div style="margin-bottom:12px">
      批量增额度:选中 <input id="batchCount" type="number" min="1" value="10" style="width:80px"> 到
      <select id="batchType"><option value="free">免费</option><option value="paid">付费</option></select>
      <button class="btn primary" onclick="batchAddQuota()">应用</button>
      <button class="btn" onclick="loadKeys()">刷新</button>
    </div>
    <table id="quotaTable"><thead><tr>
      <th><input type="checkbox" onchange="toggleAll(this)"></th>
      <th>agent</th><th>apikey</th><th>免费剩余</th><th>付费剩余</th><th>操作</th>
    </tr></thead><tbody></tbody></table>
  </div>
</main>

<div class="modal-mask" id="createModal">
  <div class="modal">
    <h3>创建 apikey</h3>
    <label>agent<select id="mAgent"></select></label>
    <label>角色<select id="mRole"><option value="normal">normal</option><option value="admin">admin</option></select></label>
    <label>免费额度<input id="mFree" type="number" value="10" min="0"></label>
    <label>付费额度<input id="mPaid" type="number" value="0" min="0"></label>
    <div class="row">
      <button class="btn" onclick="closeModal('createModal')">取消</button>
      <button class="btn primary" onclick="submitCreate()">创建</button>
    </div>
  </div>
</div>

<script>
const API = location.origin + "/api/v1/admin";
let token = "";
let keys = [];
const $ = id => document.getElementById(id);

async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {}, { "Authorization": "Bearer " + token });
  const r = await fetch(API + path, Object.assign({}, opts, { headers }));
  if (r.status === 403) { $("msg").textContent = "403:需要超级管理员权限"; throw new Error("403"); }
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({}))).detail || r.status;
    throw new Error(detail);
  }
  return r.json();
}

function login() {
  token = $("adminKey").value.trim();
  api("/agents").then(a => {
    $("msg").textContent = "登录成功:" + a.agents.length + " 个 agent";
    loadAll();
  }).catch(e => $("msg").textContent = "登录失败:" + e.message);
}

function showTab(name, btn) {
  document.querySelectorAll("nav button").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  btn.classList.add("active");
  $("tab-" + name).classList.add("active");
  if (name === "report") loadReport();
  if (name === "quota") renderQuota();
}

function loadAll() { loadAgents(); loadKeys(); }
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

function loadAgents() {
  return api("/agents").then(a => {
    const opts = a.agents.map(x => `<option value="${esc(x.agent)}">${esc(x.agent)} (${x.key_count})</option>`).join("");
    $("mAgent").innerHTML = opts;
    $("repAgent").innerHTML = '<option value="">全部</option>' + opts;
  });
}

function loadKeys() {
  return api("/apikeys").then(d => { keys = d.keys; renderQuota(); return renderKeys(); });
}

function renderKeys() {
  $("keyTable").querySelector("tbody").innerHTML = keys.map(k => `
    <tr>
      <td>${esc(k.agent)}</td>
      <td><code>${esc(k.apikey)}</code></td>
      <td><span class="badge ${esc(k.role)}">${esc(k.role)}</span></td>
      <td><span class="badge ${esc(k.status)}">${esc(k.status)}</span></td>
      <td>${k.free.used}/${k.free.remaining}</td>
      <td>${k.paid.used}/${k.paid.remaining}</td>
      <td>
        <button class="btn" onclick="editKey('${esc(k.apikey)}','${esc(k.agent)}')">改 key</button>
        <button class="btn" onclick="toggleRole('${esc(k.apikey)}','${esc(k.agent)}','${esc(k.role)}')">${k.role === "admin" ? "降为 normal" : "设为 admin"}</button>
        <button class="btn danger" onclick="delKey('${esc(k.apikey)}','${esc(k.agent)}')">删除</button>
      </td>
    </tr>`).join("");
}

function openCreate() { $("createModal").classList.add("show"); }
function closeModal(id) { $(id).classList.remove("show"); }
function submitCreate() {
  const body = { agent: $("mAgent").value, role: $("mRole").value,
                 free_quota: +$("mFree").value, paid_quota: +$("mPaid").value };
  api("/apikeys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then(k => { closeModal("createModal"); $("msg").textContent = "已创建 " + k.apikey; loadKeys(); })
    .catch(e => alert("创建失败:" + e.message));
}

function editKey(apikey, agent) {
  const nk = prompt("新 apikey(sk- 开头 + 6-64 位字母数字)", "sk-");
  if (!nk) return;
  api("/apikeys", { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apikey, agent, new_apikey: nk }) })
    .then(() => { $("msg").textContent = "已换 key"; loadKeys(); }).catch(e => alert(e.message));
}
function toggleRole(apikey, agent, role) {
  api("/apikeys", { method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apikey, agent, role: role === "admin" ? "normal" : "admin" }) })
    .then(() => { $("msg").textContent = "已改角色"; loadKeys(); }).catch(e => alert(e.message));
}
function delKey(apikey, agent) {
  if (!confirm("确认软删 " + apikey + " ?")) return;
  api("/apikeys", { method: "DELETE", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apikey, agent }) })
    .then(() => { $("msg").textContent = "已删除"; loadKeys(); }).catch(e => alert(e.message));
}
function addQuota(apikey, agent, type) {
  const c = +prompt("增加额度数", "10");
  if (!c || c <= 0) return;
  api("/apikeys/quota", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apikey, agent, type, count: c }) })
    .then(() => { $("msg").textContent = "已增加"; loadKeys(); }).catch(e => alert(e.message));
}

function renderQuota() {
  $("quotaTable").querySelector("tbody").innerHTML = keys.filter(k => k.status === "active").map(k => {
    const freeR = k.free.remaining, paidR = k.paid.remaining;
    const flag = freeR + paidR <= 5 ? ' <span class="badge deleted">低额度</span>' : "";
    return `
    <tr>
      <td><input type="checkbox" value="${esc(k.apikey)}" data-agent="${esc(k.agent)}"></td>
      <td>${esc(k.agent)}</td>
      <td><code>${esc(k.apikey)}</code></td>
      <td class="${freeR <= 5 ? "low" : ""}">${freeR}</td>
      <td class="${paidR <= 5 ? "low" : ""}">${paidR}</td>
      <td>
        <button class="btn" onclick="addQuota('${esc(k.apikey)}','${esc(k.agent)}','free')">+免费</button>
        <button class="btn" onclick="addQuota('${esc(k.apikey)}','${esc(k.agent)}','paid')">+付费</button>
        ${flag}
      </td>
    </tr>`;
  }).join("");
}
function toggleAll(cb) { document.querySelectorAll("#quotaTable tbody input[type=checkbox]").forEach(x => x.checked = cb.checked); }
function batchAddQuota() {
  const checked = [...document.querySelectorAll("#quotaTable tbody input[type=checkbox]:checked")];
  if (!checked.length) return alert("请先勾选 apikey");
  const count = +$("batchCount").value, type = $("batchType").value;
  Promise.all(checked.map(x => api("/apikeys/quota", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apikey: x.value, agent: x.dataset.agent, type, count }) })))
    .then(() => { $("msg").textContent = "已批量增加 " + count + " 到 " + checked.length + " 个 key"; loadKeys(); })
    .catch(e => alert(e.message));
}

function loadReport() {
  const agent = $("repAgent").value, days = $("repDays").value || 30;
  Promise.all([
    api("/report/summary" + (agent ? "?agent=" + encodeURIComponent(agent) : "")),
    api("/report/history" + (agent ? "?agent=" + encodeURIComponent(agent) : "") + "&days=" + days),
  ]).then(([s, h]) => { renderSummary(s); renderChart(h.series); });
}

function renderSummary(s) {
  $("summaryCards").innerHTML = s.agents.map(a => `
    <div class="card"><h3>${esc(a.agent)}</h3>
      <div>key 数:<span class="num">${a.key_count}</span></div>
      <div>免费 ${a.free_used}/${a.free_remaining}</div>
      <div>付费 ${a.paid_used}/${a.paid_remaining}</div>
    </div>`).join("");
  const t = s.total;
  $("summaryCards").insertAdjacentHTML("beforeend", `
    <div class="card"><h3>合计</h3>
      <div>key 数:<span class="num">${t.key_count}</span></div>
      <div>免费 ${t.free_used}/${t.free_remaining}</div>
      <div>付费 ${t.paid_used}/${t.paid_remaining}</div>
    </div>`);
}

function renderChart(series) {
  const cv = $("histChart"), ctx = cv.getContext("2d");
  const W = cv.width = 1000, H = cv.height = 260, padL = 40, padB = 24;
  const groups = {};
  series.forEach(s => { (groups[s.date] = groups[s.date] || []).push(s.committed); });
  const dates = Object.keys(groups).sort();
  const totals = dates.map(d => groups[d].reduce((a, b) => a + b, 0));
  const max = Math.max(1, ...totals);
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = "#d8d8d8"; ctx.fillStyle = "#222"; ctx.font = "11px sans-serif";
  for (let i = 0; i <= 4; i++) {
    const y = H - padB - (i / 4) * (H - padB - 20), v = Math.round(max * i / 4);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W, y); ctx.stroke();
    ctx.fillText(String(v), 4, y + 4);
  }
  const bw = dates.length ? (W - padL - 8) / dates.length : 0;
  dates.forEach((d, i) => {
    const v = totals[i], bh = (v / max) * (H - padB - 20);
    ctx.fillStyle = "#2563eb";
    ctx.fillRect(padL + i * bw + 2, H - padB - bh, Math.max(2, bw - 4), bh);
    ctx.fillStyle = "#222"; ctx.textAlign = "center";
    ctx.fillText(d.slice(5), padL + i * bw + bw / 2, H - 8);
  });
  ctx.textAlign = "left";
}
</script>
</body>
</html>
```

- [ ] **Step 2: 起服务验证接口连通**

Run: `ADMIN_APIKEY=sk-super DB_BACKEND=sqlite DB_SQLITE_PATH=/tmp/admin_test.db uvicorn common.admin_api:app --port 8080 &`
然后:
```bash
curl -s http://127.0.0.1:8080/api/v1/admin/agents -H "Authorization: Bearer sk-super"
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/   # 期望 200,admin.html
curl -s -X POST http://127.0.0.1:8080/api/v1/admin/apikeys -H "Authorization: Bearer sk-super" -H "Content-Type: application/json" -d '{"agent":"sentiment"}'
```
Expected: agents 返回 JSON,`/` 200 HTML,创建成功返回 `{"apikey":"sk-...","free_quota":10,...}`。验证后 kill 该进程。

- [ ] **Step 3: 浏览器实测(webapp-testing)**

用 webapp-testing skill 起 Playwright 访问 `http://127.0.0.1:8080/`:
- 输错 apikey → 403 提示
- 输入 `sk-super` 登录 → 三 tab 可见
- apikey tab:创建(选 agent/角色/额度)→ 表格出现新 key;改角色/改 key/删除 均生效
- 报表 tab:summary 卡片 + 柱状图渲染
- 额度 tab:勾选多 key 批量增额度,低额度高亮
Expected: 全流程无报错,数据与后端一致

- [ ] **Step 4: Commit**

```bash
git add web/admin.html
git commit -m "feat: 管理控制台前端(三 tab:apikey/报表/额度,无 CDN canvas 图表)"
```

---

### Task 7: 收尾(全量测试 + CHANGELOG)

**Files:**
- Modify: `CHANGELOG.md`(根项目级区)

- [ ] **Step 1: 全量测试**

Run: `pytest -q`
Expected: 全 PASS(含既有 sentiment/contract/kingdee 测试,确认无回归)

- [ ] **Step 2: 更新 CHANGELOG**

根 `CHANGELOG.md` 项目级区追加本次改动(功能 + 日期 + commit 引用),内容:

```markdown
- 管理控制台(admin_console):跨 agent apikey 管理/角色切换/额度/报表(summary+按天 committed 趋势),`uvicorn common.admin_api:app` 起,`web/admin.html` 单文件三 tab。复用 common 计费组件,超级管理员(ADMIN_APIKEY)专用。
- common/auth.py:新 `is_super_admin`。
- common/apikey_mgmt.py:`create_apikey` 加额度参数、新 `set_role/list_keys/list_agents`、`deactivate_apikey` 超管放行。
- common/billing.py:新 `report_summary`(仅 active)/`report_history`(committed 按天)。
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: CHANGELOG bump(管理控制台 admin_console)"
```

- [ ] **Step 4: 验证收尾**

Run: `git log --oneline -8`
Expected: 6 个功能/测试 commit + 1 个 docs commit,工作树干净(`git status` 无未提交)。
