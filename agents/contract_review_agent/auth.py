"""独立 apikey 鉴权(contract 独立体系,不复用 sentiment auth)。

设计 §5/§7:contract 用户需单独创建 apikey,额度与 sentiment 互不影响;
独立 apikey 管理接口 POST /api/v1/apikeys(创建/修改/删除,管理员)。

待实现:
  - check_apikey(请求头 apikey 校验 + 资源归属)
  - require_admin(管理员 ADMIN_APIKEY 放行,可查全部用户额度)
  - 存储访问统一走 common/db.py(MySQL 生产 / SQLite 测试双后端),业务代码不直接连库

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
