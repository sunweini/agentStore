"""contract-review-agent 包:合同审核 Agent(F1 审核 prompt 优化 + F2 合同章节审核)。

双功能:
  - F1:合同类型 + 原始审核 prompt → 结构化审核 prompt(含类型常见风险点 +
       法规引用指引,产物可作 F2 的审核要求复用)。
  - F2:上传 word/pdf 合同 → 按章节审核,输出每处问题的原文位置/问题描述/
       改进建议/法律依据,按固定格式返回。

核心约束:反幻觉铁律 —— 所有法律依据必须可回溯到法条库原文,不允许编造。

设计见 docs/superpowers/specs/2026-08-13-contract-review-agent-design.md。
实现后按需导入:
  #   from agents.contract_review_agent.agent import build_graph
"""
