"""LLM 多提供商适配器 — 统一管理 OpenAI / Claude / Gemini / Azure。

职责：
1. 根据配置自动选择 LLM Provider
2. 统一管理 API Key、base_url、模型名称
3. 记录 token 使用量与成本

用法：
    from src.infra.model_adapter import get_llm, get_deep_think_llm
    llm = get_llm()  # 默认快速思考模型
    deep = get_deep_think_llm()  # 深度思考模型
"""

from __future__ import annotations

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_openai import AzureChatOpenAI, ChatOpenAI

from src.infra.config import cfg
from src.infra.logger import get_logger

logger = get_logger(__name__)


class LLMUsageTracker:
    """简单的 Token 使用追踪器。"""

    def __init__(self) -> None:
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.total_cost = 0.0
        self.calls = 0

    def record(self, tokens_in: int, tokens_out: int, model: str = "unknown") -> None:
        self.calls += 1
        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        # 粗略估算成本（USD）
        cost_per_1k = self._estimate_cost(model)
        cost = (tokens_in + tokens_out) / 1000 * cost_per_1k
        self.total_cost += cost
        logger.debug("LLM call #%d | %s | in=%d out=%d | cost=$%.4f", self.calls, model, tokens_in, tokens_out, cost)

    def _estimate_cost(self, model: str) -> float:
        """每 1K tokens 的估算成本 (USD)。"""
        model_lower = model.lower()
        if "gpt-4o-mini" in model_lower:
            return 0.0015
        elif "gpt-4o" in model_lower:
            return 0.005
        elif "claude-3-5" in model_lower or "claude-3.5" in model_lower:
            return 0.003
        elif "claude-3" in model_lower:
            return 0.015
        elif "gemini" in model_lower:
            return 0.0005  # Gemini 非常便宜
        return 0.01

    @property
    def total_tokens(self) -> tuple[int, int]:
        return self.total_tokens_in, self.total_tokens_out


# 全局追踪器实例
_usage_tracker = LLMUsageTracker()


def get_usage_tracker() -> LLMUsageTracker:
    return _usage_tracker


def get_llm(model_name: str | None = None, temperature: float = 0.3) -> Any:
    """获取默认 LLM 实例（快速思考模型）。"""
    provider = cfg().get("default_llm_provider", "openai")
    model = model_name or cfg().get("quick_think_model", "gpt-4o-mini")
    return _create_llm(provider, model, temperature)


def get_deep_think_llm(model_name: str | None = None, temperature: float = 0.2) -> Any:
    """获取深度思考 LLM 实例。"""
    provider = cfg().get("default_llm_provider", "openai")
    model = model_name or cfg().get("deep_think_model", "gpt-4o")
    return _create_llm(provider, model, temperature)


def _create_llm(provider: str, model: str, temperature: float) -> Any:
    """根据 provider 创建对应的 LangChain LLM 实例。"""
    provider_lower = provider.lower()

    if provider_lower == "openai":
        api_key = cfg().get("openai_api_key")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not configured")
        kwargs = dict(model=model, api_key=api_key)
        if "gpt-5" not in model:
            kwargs["temperature"] = temperature
        return ChatOpenAI(**kwargs)

    elif provider_lower == "anthropic":
        api_key = cfg().get("anthropic_api_key")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not configured")
        return ChatAnthropic(
            model=model,
            api_key=api_key,
            temperature=temperature,
        )

    elif provider_lower == "azure":
        endpoint = cfg().get("azure_endpoint")
        api_key = cfg().get("azure_api_key")
        api_version = cfg().get("azure_api_version", "2025-01-01-preview")
        if not endpoint or not api_key:
            raise ValueError("Azure OpenAI credentials not configured")
        # Azure 模型名通常不带前缀，需要 deployment_name
        kwargs = dict(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            deployment_name=model,
        )
        # gpt-5.3-chat (Azure reasoning models) do not accept temperature
        if "gpt-5" not in model:
            kwargs["temperature"] = temperature
        return AzureChatOpenAI(**kwargs)

    elif provider_lower == "google":
        # 需要 langchain-google-genai
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            api_key = cfg().get("google_api_key")
            if not api_key:
                raise ValueError("GOOGLE_API_KEY not configured")
            return ChatGoogleGenerativeAI(
                model=model,
                google_api_key=api_key,
                temperature=temperature,
            )
        except ImportError:
            raise ImportError("Please install langchain-google-genai for Google provider")

    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")
