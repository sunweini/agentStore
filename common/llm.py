"""多供应商模型工厂:按供应商注册表初始化 ChatModel。

设计见 docs/superpowers/specs/2026-08-06-sentiment-query-agent-langgraph-design.md 第 4 节。

换供应商:改 .env 的 LLM_PROVIDER 即可,代码不动。
加供应商:注册表加一项 + requirements.txt 加包。
"""

from __future__ import annotations

from typing import Callable

from langchain_core.language_models import BaseChatModel

from common import config

# 供应商 → 模型构建器。新增供应商在这里注册。
_REGISTRY: dict[str, Callable[[str], BaseChatModel]] = {}


def _build_openai_compatible(provider: str) -> Callable[[str], BaseChatModel]:
    """生成 OpenAI 兼容供应商(DeepSeek 等)的构建器。

    经 langchain-openai 的 ChatOpenAI 接入。
    接入前提:.env 配置了 <PROVIDER>_API_KEY,以及 <PROVIDER>_BASE_URL
    (OpenAI 官方可省,默认官方端点)。
    """

    def _build(model_id: str) -> BaseChatModel:
        from langchain_openai import ChatOpenAI

        api_key = config.provider_api_key(provider)
        if not api_key:
            raise ValueError(
                f"{provider.upper()}_API_KEY 未配置。"
                "复制 .env.example 为 .env 并填入密钥。"
            )
        base_url = config.provider_base_url(provider)
        return ChatOpenAI(
            model=model_id,
            api_key=api_key,
            base_url=base_url or None,
        )

    return _build


# DeepSeek 走 OpenAI 兼容端点。
# OpenAI 官方、OpenRouter、Moonshot 等同为 OpenAI 兼容供应商,后续按需注册。
_REGISTRY["deepseek"] = _build_openai_compatible("deepseek")


def get_chat_model(
    provider: str | None = None,
    model_id: str | None = None,
) -> BaseChatModel:
    """供应商驱动模型工厂。

    Args:
        provider: 供应商名(注册表 key)。缺省用 .env 的 LLM_PROVIDER。
        model_id: 模型 ID。缺省用 LLM_MODEL / <PROVIDER>_MODEL,
                  再缺省用供应商默认模型。

    Returns:
        配置好的 ChatModel 实例。

    Raises:
        ValueError: 供应商未注册,或密钥未配置。
    """
    provider = provider or config.current_provider()
    builder = _REGISTRY.get(provider)
    if builder is None:
        raise ValueError(
            f"供应商 {provider!r} 未注册。已注册: {sorted(_REGISTRY)}。"
        )
    model_id = model_id or config.current_model(provider)
    return builder(model_id)
