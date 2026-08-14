"""公共 apikey 管理:创建/换 key/停用 + 每 agent 管理员引导。

统一表 agent_api_keys(复合主键 apikey+agent),设计见
docs/superpowers/specs/2026-08-14-common-billing-component-design.md §3/§4。

- create_apikey(agent, name, role):服务端随机 `sk-`+token(不给调用方铸 key 权限),
  role 白名单 normal/admin,非法 ValueError(防任意调用方铸 admin 后门)。
- update_apikey(agent, old, new):换 key —— 新 key 须 `sk-[A-Za-z0-9]{6,64}` 格式
  (非法 400,与 create 语义对齐);仅改主键 apikey 列,额度/status/role
  自然继承;流水 agent_billing_records.apikey 一并重写。不做 sentiment 特有的
  方案组文件 owner 迁移(那是 agent 层业务,公共组件只管 DB 行)。
- deactivate_apikey(agent, apikey, admin):软删(status='deleted'),统一 contract 规则
  —— require_admin 授权后:不可停用自己(403),admin 目标可停用(防"被铸 admin
  永不可停用"后门;ensure_admin 幂等兜底可重建,无全停死风险)。
- admin_list(apikey, agent):管理员查该 agent 全部 apikey(含额度使用,软删的也列出)。
- ensure_admin(agent):每 agent 首个管理员引导(额度 99999999),幂等 —— 该 agent
  已有 active 管理员行则跳过(迁移后旧管理员行已存在,重复启动不重复插入;软删的
  admin 不计数,保证现存 admin 全被软删时能重建,无"全停死"风险)。读 .env
  `ADMIN_APIKEY`(sentiment 兼容);未配置则自动生成 sk-+token 并记结构化日志。

存储访问统一走 common/db.py(MySQL 生产 / SQLite 测试双后端)。
"""

from __future__ import annotations

import logging
import re
import secrets

from fastapi import HTTPException

from common import config, db
from common.auth import require_admin
from common.billing import _ADMIN_FREE_QUOTA

logger = logging.getLogger(__name__)

_DEFAULT_FREE_QUOTA = 10
_DEFAULT_PAID_QUOTA = 0
_ALLOWED_ROLES = ("normal", "admin")


def _gen_apikey() -> str:
    """生成随机 apikey:sk- + 32 位十六进制(格式兼容 sk-[A-Za-z0-9])。"""
    return f"sk-{secrets.token_hex(16)}"


def _mask_apikey(apikey: str) -> str:
    """apikey 脱敏:保留 `sk-` 前缀 + 前 4 位 + `***` + 后 4 位,如 `sk-abcd***wxyz`。

    凭据只允许以脱敏形态进日志/响应(OBS-CORE-003 敏感信息最小暴露)。过短(<12)
    整体遮蔽为 `sk-***`,避免中段泄露。
    """
    if len(apikey) < 12:
        return "sk-***"
    return f"{apikey[:7]}***{apikey[-4:]}"


def _get_row(apikey: str, agent: str) -> dict | None:
    """按复合主键查 agent_api_keys 行,无行返回 None。"""
    rows = db.query("SELECT * FROM agent_api_keys WHERE apikey=%s AND agent=%s",
                    (apikey, agent))
    return rows[0] if rows else None


def create_apikey(agent: str, name: str, role: str = "normal") -> dict:
    """创建 apikey(默认免费 10 / 付费 0),返回 {apikey, name, role, free_quota, paid_quota}。

    name 作为创建时标签仅出现在返回值中(表结构无 name 列,不落库)。
    role 仅允许 normal/admin,非法值抛 ValueError(防任意调用方铸 admin 后门)。
    随机 apikey 冲突(理论极小)时递归重试一次。
    """
    if role not in _ALLOWED_ROLES:
        raise ValueError(f"非法 role: {role}(仅允许 {'/'.join(_ALLOWED_ROLES)})")
    for _ in range(3):
        apikey = _gen_apikey()
        try:
            db.execute(
                "INSERT INTO agent_api_keys (apikey, agent, role, status, free_quota, paid_quota) "
                "VALUES (%s, %s, %s, 'active', %s, %s)",
                (apikey, agent, role, _DEFAULT_FREE_QUOTA, _DEFAULT_PAID_QUOTA),
            )
            break
        except RuntimeError as exc:
            if "Duplicate" in str(exc) or "1062" in str(exc) or "UNIQUE" in str(exc):
                continue  # 随机 key 撞唯一键(理论极小),限次重试
            raise
    else:
        raise RuntimeError("apikey 生成冲突:3 次重试仍撞唯一键")
    return {
        "apikey": apikey,
        "name": name,
        "role": role,
        "free_quota": _DEFAULT_FREE_QUOTA,
        "paid_quota": _DEFAULT_PAID_QUOTA,
    }


def update_apikey(agent: str, old_apikey: str, new_apikey: str) -> dict:
    """换 key:旧 key → 新 key,额度/status/role 继承 + 流水 apikey 重写。

    新 key 须 `sk-[A-Za-z0-9]{6,64}` 格式,非法 400(与 create 语义对齐)。
    仅 UPDATE 主键列 apikey,其余列原样保留即"继承"。管理员 key 不可换(403),
    已软删 key 不可换(400)。方案组文件 owner 迁移为 sentiment 特有,公共版不做。
    """
    if not re.fullmatch(r"sk-[A-Za-z0-9]{6,64}", new_apikey):
        raise HTTPException(status_code=400, detail="apikey 格式:sk- 开头 + 6-64 位字母数字")

    old_row = _get_row(old_apikey, agent)
    if old_row is None:
        raise HTTPException(status_code=404, detail="原 apikey 不存在")
    if old_row["role"] == "admin":
        raise HTTPException(status_code=403, detail="不可修改管理员 apikey")
    if old_row["status"] != "active":
        raise HTTPException(status_code=400, detail="原 apikey 已删除,不可修改")

    if _get_row(new_apikey, agent) is not None:
        raise HTTPException(status_code=409, detail="新 apikey 已存在")

    # 事务:换主键 + 流水迁移,同 agent 维度,原子完成
    @db.transaction
    def _do(cur, exec) -> None:
        exec("UPDATE agent_api_keys SET apikey=%s WHERE apikey=%s AND agent=%s",
             (new_apikey, old_apikey, agent))
        exec("UPDATE agent_billing_records SET apikey=%s "
             "WHERE apikey=%s AND agent=%s", (new_apikey, old_apikey, agent))

    _do()
    return {"old_apikey": old_apikey, "new_apikey": new_apikey, "migrated": True}


def deactivate_apikey(agent: str, apikey: str, admin: str) -> None:
    """软删 apikey:status='deleted',鉴权即拒绝,数据保留。

    统一 contract 规则:调用方(admin)须经 require_admin 授权;放开对 admin 目标的
    停用(堵"被铸 admin 永不可停用"后门),仅保留"不可停用自己"守卫防误删自身凭据。
    """
    require_admin(admin, agent)
    if apikey == admin:
        raise HTTPException(status_code=403, detail="不可停用自己")
    if _get_row(apikey, agent) is None:
        raise HTTPException(status_code=404, detail="apikey 不存在")
    db.execute("UPDATE agent_api_keys SET status='deleted' "
               "WHERE apikey=%s AND agent=%s", (apikey, agent))


def admin_list(apikey: str, agent: str) -> list[dict]:
    """管理员:查该 agent 全部 apikey 的额度使用(含 status,软删的也列出)。"""
    require_admin(apikey, agent)
    rows = db.query(
        "SELECT apikey, agent, role, status, free_quota, free_used, paid_quota, paid_used "
        "FROM agent_api_keys WHERE agent=%s ORDER BY apikey", (agent,))
    return [
        {
            "apikey": r["apikey"],
            "agent": r["agent"],
            "role": r["role"],
            "status": r["status"],
            "free": {"total": r["free_quota"], "used": r["free_used"],
                     "remaining": r["free_quota"] - r["free_used"]},
            "paid": {"total": r["paid_quota"], "used": r["paid_used"],
                     "remaining": r["paid_quota"] - r["paid_used"]},
        }
        for r in rows
    ]


def ensure_admin(agent: str) -> None:
    """每 agent 首个管理员引导(额度 99999999),幂等。

    幂等语义:该 agent 已有 **active** 管理员行(任意 key)即跳过 —— 迁移后旧管理员行
    已存在,重复启动不重复插入;无 ADMIN_APIKEY 自动生成路径也靠此防重复(两次启动各
    生成不同 key 会铸两行 admin,故必须按 agent 判重而非按 key)。
    status='active' 过滤是安全前提:软删的 admin 不计数,现存 admin 全被软删时
    ensure_admin 可重建,否则"全停死"无法自愈(spec §2.5 契约)。
    读 .env ADMIN_APIKEY(sentiment 兼容);未配置则自动生成并记结构化日志。
    """
    if db.query("SELECT apikey FROM agent_api_keys "
                "WHERE agent=%s AND role='admin' AND status='active'", (agent,)):
        return
    key = config.get_env("ADMIN_APIKEY")
    if not key:
        key = _gen_apikey()
        logger.info("service=common component=apikey_mgmt event=admin_auto_generated "
                    "agent=%s apikey=%s role=admin", agent, _mask_apikey(key))
    existing = _get_row(key, agent)
    if existing is not None:
        # 该 key 已存在行(ADMIN_APIKEY 与存量 key 撞车 / 自动生成撞车理论极小)→ 跳过,
        # 避免 INSERT 撞复合主键;warning 便于排查 ADMIN_APIKEY 配错。
        logger.warning("service=common component=apikey_mgmt event=admin_key_collision "
                       "agent=%s apikey=%s existing_role=%s existing_status=%s",
                       agent, _mask_apikey(key), existing["role"], existing["status"])
        return
    db.execute(
        "INSERT INTO agent_api_keys (apikey, agent, role, status, free_quota, paid_quota) "
        "VALUES (%s, %s, 'admin', 'active', %s, %s)",
        (key, agent, _ADMIN_FREE_QUOTA, _DEFAULT_PAID_QUOTA),
    )
