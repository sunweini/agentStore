"""测试环境隔离。

EMBEDDING_* 清理:真实环境 .env 可能把 EMBEDDING_PROVIDER 配成
openai-compatible(远程服务),而 common.config 在导入时会把 .env 写入
os.environ —— 若不清理,RagClient 相关测试会真连远程 embedding 服务
(慢、依赖网络)。autouse 夹具在**每个测试前**清除 EMBEDDING_* 并清空
_embedding_model 的 lru_cache,保证全部测试确定性走 huggingface 本地
默认;env 分支的专项测试在用例内显式设置 EMBEDDING_* 并自行清理。
"""

import pytest

from common import rag


@pytest.fixture(autouse=True)
def _clear_embedding_env(monkeypatch):
    """清除 EMBEDDING_* 环境变量 + 清空嵌入模型单例缓存。"""
    for key in (
        "EMBEDDING_PROVIDER",
        "EMBEDDING_MODEL",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    rag._embedding_model.cache_clear()
    yield
    rag._embedding_model.cache_clear()
