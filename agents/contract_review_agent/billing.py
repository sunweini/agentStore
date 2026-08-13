"""独立配额与计费(contract_api_keys / contract_billing_records,与 sentiment 完全隔离)。

设计 §5:不复用 sentiment 的 billing/auth —— 同库(agentstore)独立表,独立 apikey,
额度与 sentiment 互不影响,业务代码不跨 agent import。
计费单位:按次 —— 一个合同文件审核完成 = 1 次扣费(先免费后付费,事务原子);
F1 prompt 优化默认不计费;pending 上限每 apikey 5。

待实现:
  - check_quota / create_pending / commit / cancel(对齐 sentiment billing 语义)
  - 存储访问统一走 common/db.py(MySQL 生产 / SQLite 测试双后端),业务代码不直接连库

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
"""
