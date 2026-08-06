# 共享层包:所有 agent 复用的基础设施(LLM 工厂、配置、prompt 加载)。

# 暂不导入子模块,避免未完成时 import 报错。
# 实现时按需导入:
#   from common.llm import get_chat_model
#   from common.config import settings
#   from common.prompts import load_prompt
