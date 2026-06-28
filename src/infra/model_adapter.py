"""LLM 多提供商适配器 — 统一管理 OpenAI / Claude / Gemini / Azure。

职责：
1. 根据配置自动选择 LLM Provider
2. 统一管理 API Key、base_url、模型名称
3. 记录 token 使用量与成本
4. 记录 API 调用延迟 (供 Dashboard 监控)

用法：
    from src.infra.model_adapter import get_llm, get_deep_think_llm
    llm = get_llm()  # 默认快速思考模型
    deep = get_deep_think_llm()  # 深度思考模型
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Dict

from langchain_anthropic import ChatAnthropic
from langchain_openai import AzureChatOpenAI, ChatOpenAI

from src.infra.config import cfg
from src.infra.llm_observability import PipelineLLMCallback
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
        cost_per_1k = self._estimate_cost(model)
        cost = (tokens_in + tokens_out) / 1000 * cost_per_1k
        self.total_cost += cost
        logger.debug(
            "LLM call #%d | %s | in=%d out=%d | cost=$%.4f",
            self.calls,
            model,
            tokens_in,
            tokens_out,
            cost,
        )

    def _estimate_cost(self, model: str) -> float:
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
            return 0.0005
        return 0.01

    @property
    def total_tokens(self) -> tuple[int, int]:
        return self.total_tokens_in, self.total_tokens_out


class APILatencyTracker:
    """记录最近 N 次 API 延迟，用于 Dashboard。"""

    def __init__(self, window: int = 20) -> None:
        self.window = window
        self.data: Dict[str, deque] = defaultdict(lambda: deque(maxlen=self.window))
        self.calls: Dict[str, int] = defaultdict(int)

    def record(self, key: str, latency_ms: float) -> None:
        self.data[key].append(round(latency_ms))
        self.calls[key] += 1

    def stats(self) -> Dict[str, dict]:
        result: Dict[str, dict] = {}
        for key, latencies in self.data.items():
            result[key] = {
                "calls": self.calls[key],
                "latency": list(latencies),
            }
        return result


class LLMProxy:
    """包装 LangChain LLM，记录调用延迟。"""

    def __init__(self, llm: Any, tracker: APILatencyTracker, key: str):
        self._llm = llm
        self._tracker = tracker
        self._key = key

    def invoke(self, *args, **kwargs):
        start = time.perf_counter()
        result = self._llm.invoke(*args, **kwargs)
        latency_ms = (time.perf_counter() - start) * 1000
        self._tracker.record(self._key, latency_ms)
        return result

    def __getattr__(self, item):
        return getattr(self._llm, item)


_usage_tracker = LLMUsageTracker()
_latency_tracker = APILatencyTracker()


def get_usage_tracker() -> LLMUsageTracker:
    return _usage_tracker


def get_api_stats() -> Dict[str, dict]:
    """返回 Dashboard 使用的 API 延迟统计。"""
    return _latency_tracker.stats()


def get_llm(
    model_name: str | None = None,
    temperature: float = 0.3,
    stage: str | None = None,
) -> Any:
    provider = cfg().get("default_llm_provider", "openai")
    model = model_name or cfg().get("quick_think_model", "gpt-4o-mini")
    return _create_llm(provider, model, temperature, stage=stage)


def get_deep_think_llm(
    model_name: str | None = None,
    temperature: float = 0.2,
    stage: str | None = None,
) -> Any:
    provider = cfg().get("default_llm_provider", "openai")
    model = model_name or cfg().get("deep_think_model", "gpt-4o")
    return _create_llm(provider, model, temperature, stage=stage)


def _create_llm(
    provider: str, model: str, temperature: float, stage: str | None = None
) -> Any:
    provider_lower = provider.lower()
    callbacks = [PipelineLLMCallback(provider=provider_lower, stage=stage)]

    # 全局超时与重试配置 — 防止 HTTP 请求无限挂起导致调度器卡死
    request_timeout = cfg().get("llm_timeout", 120)  # 秒
    max_retries = cfg().get("llm_max_retries", 3)

    if provider_lower == "openai":
        api_key = cfg().get("openai_api_key")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not configured")
        kwargs = dict(
            model=model,
            api_key=api_key,
            callbacks=callbacks,
            timeout=request_timeout,
            max_retries=max_retries,
        )
        base_url = cfg().get("openai_base_url")
        if base_url:
            kwargs["base_url"] = base_url
        # 某些模型不支持自定义 temperature（如 gpt-5, gpt-chat-latest, o1, o3）
        _no_temp_models = ("gpt-5", "gpt-chat-latest", "o1", "o3")
        if not any(m in model for m in _no_temp_models):
            kwargs["temperature"] = temperature
        llm = ChatOpenAI(**kwargs)

    elif provider_lower == "anthropic":
        api_key = cfg().get("anthropic_api_key")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not configured")
        llm = ChatAnthropic(
            model=model,
            api_key=api_key,
            temperature=temperature,
            callbacks=callbacks,
            timeout=float(request_timeout),
            max_retries=max_retries,
        )

    elif provider_lower == "azure":
        endpoint = cfg().get("azure_endpoint")
        api_key = cfg().get("azure_api_key")
        api_version = cfg().get("azure_api_version", "2025-01-01-preview")
        if not endpoint or not api_key:
            raise ValueError("Azure OpenAI credentials not configured")

        kwargs = dict(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            deployment_name=model,
            callbacks=callbacks,
            timeout=request_timeout,
            max_retries=max_retries,
        )

        if "gpt-5" not in model:
            kwargs["temperature"] = temperature

        llm = AzureChatOpenAI(**kwargs)

    elif provider_lower == "google":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI

            api_key = cfg().get("google_api_key")
            if not api_key:
                raise ValueError("GOOGLE_API_KEY not configured")

            llm = ChatGoogleGenerativeAI(
                model=model,
                google_api_key=api_key,
                temperature=temperature,
                callbacks=callbacks,
                timeout=request_timeout,
                max_retries=max_retries,
            )
        except ImportError:
            raise ImportError("Please install langchain-google-genai for Google provider")

    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")

    key = f"{provider_lower}:{model}"
    return LLMProxy(llm, _latency_tracker, key)
