"""独立 apikey 管理(contract 独立表,参照 sentiment apikey_mgmt 独立实现)。

设计 §5/§7:apikey 创建(默认免费/付费额度)/ 修改(换 key 资费继承 + 任务迁移)/
删除(软删,数据保留)+ 管理员初始化;额度与 sentiment 互不影响。

待实现:
  - create_apikey / update_apikey / deactivate_apikey / admin_list(管理员)
  - 存储访问统一走 common/db.py(MySQL 生产 / SQLite 测试双后端),业务代码不直接连库

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
