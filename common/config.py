"""配置加载:统一从 .env / 环境变量读取配置,密钥不硬编码。

变量约定(见 .env.example):
  LLM_PROVIDER=<provider>            当前默认供应商
  LLM_MODEL=<model>                  当前默认模型
  <PROVIDER>_API_KEY=<key>           供应商密钥
  <PROVIDER>_BASE_URL=<url>          供应商 API 地址(OpenAI 兼容供应商)
  <PROVIDER>_MODEL=<model>           供应商默认模型
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录的 .env 存在才加载,避免 CI/生产无 .env 时报错。
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)


def get_env(name: str, default: str = "") -> str:
    """读取环境变量;缺省返回 default(空串表示未配置)。

    统一走这里,便于以后集中加校验/默认值。
    """
    return os.getenv(name, default)


def current_provider() -> str:
    """当前默认供应商(LLM_PROVIDER,缺省 deepseek)。"""
    return get_env("LLM_PROVIDER", "deepseek")


def current_model(provider: str | None = None) -> str:
    """当前默认模型。

    优先级:显式 LLM_MODEL > <PROVIDER>_MODEL > 空。
    返回空串时由 llm.py 回退到供应商硬编码默认。
    """
    provider = provider or current_provider()
    return get_env("LLM_MODEL") or get_env(f"{provider.upper()}_MODEL")


def provider_api_key(provider: str) -> str:
    """供应商 API 密钥。未配置返回空串,llm.py 据此报清晰错误。"""
    return get_env(f"{provider.upper()}_API_KEY")


def provider_base_url(provider: str) -> str:
    """供应商 API 地址。未配置返回空串。"""
    return get_env(f"{provider.upper()}_BASE_URL")


_KD_VAR_NAMES = ("KD_BASE_URL", "KD_USERNAME", "KD_PASSWORD",
                 "KD_DATA_CENTER", "KD_LCID")


def kingdee_env_vars(env: str = "") -> dict:
    """按环境取金蝶凭证:优先 <VAR>_<ENV>,回落 <VAR>(默认环境)。

    含 KD_LCID(语系);env 空 = 默认环境,直接用 KD_* 5 项。
    客户端按返回值构造,缺项由调用方(硬门槛)报 503 点明。
    """
    prefix = f"_{env.upper()}" if env else ""
    return {name: get_env(f"{name}{prefix}") for name in _KD_VAR_NAMES}
