"""workers 包:各环节 worker 统一基类。

- WorkerBase:契约/上报/状态机一次实现,子类只写 _execute(见 base.py)
- 后续 worker(规划/设计/编码/编译/评审/冒烟/打包)继承本基类,
  _execute 返回 {status, artifact_key, path, evidence, concerns}
"""
from agents.kingdee_plugin_agent.graph.workers.base import WorkerBase

__all__ = ["WorkerBase"]
