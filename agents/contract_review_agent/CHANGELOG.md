# contract-review-agent 版本更新说明(CHANGELOG)

> 版本号独立管理(每 agent 独立序列)。
> 收尾规则:改动归本 agent → 更新本文件 + bump 版本号(当前最大号 +1)。

---

## v0.1.0 — 2026-08-13(项目初始化:目录结构 + 占位文档 + langgraph 注册)

### 新增

- **骨架**:`agents/contract_review_agent/` 目录结构,全部 .py 写占位 docstring
  (职责 / 待实现 / 设计文档引用),不含实现代码
- **双功能定义**:F1 审核 prompt 优化 + F2 合同章节审核(详见设计文档
  `docs/superpowers/specs/2026-08-13-contract-review-agent-design.md`)
- **langgraph.json 注册**:`contract_review_agent` → `agent.py:build_graph`
  (保留现有 sentiment-query-agent / kingdee_plugin_agent 注册不动)
- **CLAUDE.md**:本 agent 职责 / 架构 / 常用操作 / 约束(反幻觉铁律 / temperature /
  大小限制 / 独立计费)
- **测试占位**:`tests/test_contract_review_agent.py`(仅包导入冒烟,行为测试待实现阶段)
