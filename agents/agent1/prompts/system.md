"""agent1 系统提示词(默认,一个就够)。

角色:通用多步骤任务助手,负责拆解任务、调用工具、汇总结果。

TODO(实现时):
- 填写 agent1 的具体角色定义与输出要求
- 复杂流程需要多 prompt 时,在此目录加 planner.md / executor.md 等,
  node 各用各的(common/prompts.py 的 load_prompt(agent, name) 加载)
"""
