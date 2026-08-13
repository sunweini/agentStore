"""测试环境隔离。

嵌入模型跟随 .env:真实环境 EMBEDDING_PROVIDER=openai-compatible(远程
Qwen3-Embedding-8B,10.33.17.234:32320),测试也用它(用户要求测试环境与
生产一致)。本夹具把 common.config 读到的 EMBEDDING_* 注入 os.environ
并清空 _embedding_model 的 lru_cache,保证确定性(不靠 load_dotenv 顺序);
若 .env 未配 EMBEDDING_*,回落到 huggingface 本地默认。env 分支专项测试
仍可在用例内显式覆盖并自行清理。
"""

import pytest

from common import rag


@pytest.fixture(autouse=True)
def _sync_embedding_env(monkeypatch):
    """注入 .env 的 EMBEDDING_* 配置(跟随生产)+ 清空嵌入模型单例缓存。"""
    from common import config

    for key in (
        "EMBEDDING_PROVIDER",
        "EMBEDDING_MODEL",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_API_KEY",
    ):
        monkeypatch.setenv(key, config.get_env(key))
    rag._embedding_model.cache_clear()
    yield
    rag._embedding_model.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_tasks_db(monkeypatch, tmp_path):
    """任务持久化 DB 隔离:每个测试独立数据文件 + 清除 KINGDEE_TASKS_DB。

    Task 5 起 create_app 默认落盘 data/kingdee-tasks.db(共享默认路径),
    测试间共享会互相污染(任务终态置位影响恢复扫描);统一重定向到
    tmp_path,且不设 KINGDEE_TASKS_DB 环境兜底(避免 .env 配置串扰)。
    """
    monkeypatch.delenv("KINGDEE_TASKS_DB", raising=False)
    monkeypatch.setenv("KINGDEE_TASKS_DB", str(tmp_path / "kingdee-tasks.db"))


@pytest.fixture(autouse=True)
def _reset_api_concurrency_sem(monkeypatch):
    """每测试前重置 API 并发闸门:模块级 Semaphore 跨测试残留,前面测试
    留下的挂起任务线程(interrupt 等待 30s 超时)占满配额后,后续测试
    恢复任务的 _sem.acquire() 主线程阻塞 → 全量套件死锁(单跑不复现,
    因为全新进程 Semaphore 满值)。重置为满配额:残留线程的 release
    打到旧实例,无害。
    """
    import threading

    import agents.kingdee_plugin_agent.api as api_mod

    monkeypatch.setattr(api_mod, "_sem",
                        threading.Semaphore(api_mod.MAX_CONCURRENT_TASKS))
