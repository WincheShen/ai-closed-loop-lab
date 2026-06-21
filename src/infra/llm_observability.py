"""Pipeline LLM observability — records every LangChain LLM call to central_brain.

This module provides ``PipelineLLMCallback``, a LangChain ``BaseCallbackHandler``
that is automatically attached by the model adapter (``get_llm`` / ``get_deep_think_llm``)
so that *all* pipeline LLM calls are persisted to the ``llm_calls`` table without
manual intervention from each caller.

Design principles:
- Non-intrusive: recording failures are logged and never propagate to the caller.
- Zero-config: the callback is attached internally by ``_create_llm`` in
  ``model_adapter.py``; pipeline code does not need to be modified.
- Context-aware: if ``llm_request_context`` (from the trading-agent observability
  module) has been set, those values are used; otherwise the callback falls back to
  the ``provider`` / ``stage`` supplied at construction time.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LangChain import guard (same pattern as observability.py)
# ---------------------------------------------------------------------------

try:
    from langchain_core.callbacks.base import BaseCallbackHandler  # type: ignore
    _HAS_LANGCHAIN = True
except Exception:  # noqa: BLE001
    BaseCallbackHandler = object  # type: ignore[misc,assignment]
    _HAS_LANGCHAIN = False


# ---------------------------------------------------------------------------
# Pricing helper (reuses the trading-agent observability pricing book)
# ---------------------------------------------------------------------------

def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Delegate to the existing pricing estimator if available."""
    try:
        from src.trading_agent_service.analysis.observability import estimate_cost_usd
        return estimate_cost_usd(model, prompt_tokens, completion_tokens)
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------------------
# Context var helpers (reads ambient context if set by trading-agent adapter)
# ---------------------------------------------------------------------------

def _get_ambient_request_id() -> Optional[str]:
    try:
        from src.trading_agent_service.analysis.observability import current_request_id
        return current_request_id()
    except Exception:  # noqa: BLE001
        return None


def _get_ambient_stage() -> Optional[str]:
    try:
        from src.trading_agent_service.analysis.observability import _stage
        return _stage.get()
    except Exception:  # noqa: BLE001
        return None


def _get_ambient_symbol() -> Optional[str]:
    try:
        from src.trading_agent_service.analysis.observability import _symbol
        return _symbol.get()
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

class PipelineLLMCallback(BaseCallbackHandler):  # type: ignore[misc]
    """Records every LLM call to ``central_brain.llm_calls``.

    Parameters
    ----------
    provider : str
        LLM provider name (e.g. "openai", "azure", "anthropic").
    stage : str | None
        Default pipeline stage label (e.g. "strategist", "market_brain").
        Overridden by ambient ``llm_request_context`` if set.
    """

    def __init__(self, provider: str, stage: Optional[str] = None) -> None:
        self._provider = provider
        self._default_stage = stage
        self._starts: dict[Any, float] = {}

    # -- LangChain hooks ---------------------------------------------------

    def on_llm_start(
        self, serialized: dict, prompts: list, **kwargs: Any
    ) -> None:
        run_id = kwargs.get("run_id")
        if run_id is not None:
            self._starts[run_id] = time.time()

    def on_chat_model_start(
        self,
        serialized: dict,
        messages: list,
        **kwargs: Any,
    ) -> None:
        """Handles ChatModel starts (ChatOpenAI uses this instead of on_llm_start)."""
        run_id = kwargs.get("run_id")
        if run_id is not None:
            self._starts[run_id] = time.time()

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        started = self._starts.pop(run_id, None)
        latency_ms = int((time.time() - started) * 1000) if started else None

        usage = self._extract_token_usage(response)
        model = self._extract_model_name(response) or "unknown"

        cost = _estimate_cost(
            model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

        self._record(
            success=True,
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            cost_usd=cost,
            latency_ms=latency_ms,
            error_msg=None,
        )

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        started = self._starts.pop(run_id, None)
        latency_ms = int((time.time() - started) * 1000) if started else None

        self._record(
            success=False,
            model="unknown",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cost_usd=0.0,
            latency_ms=latency_ms,
            error_msg=str(error)[:500],
        )

    # -- Internal ----------------------------------------------------------

    def _record(
        self,
        *,
        success: bool,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost_usd: float,
        latency_ms: Optional[int],
        error_msg: Optional[str],
    ) -> None:
        try:
            from central_brain import get_central_brain

            # Prefer ambient context (set by llm_request_context) over defaults
            request_id = _get_ambient_request_id() or str(uuid.uuid4())
            stage = _get_ambient_stage() or self._default_stage
            symbol = _get_ambient_symbol()

            get_central_brain().store.record_llm_call(
                request_id=request_id,
                symbol=symbol,
                stage=stage,
                model=model,
                provider=self._provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                success=success,
                error_msg=error_msg,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("PipelineLLMCallback record failed (non-fatal): %s", e)

    # -- Response parsing helpers ------------------------------------------

    @staticmethod
    def _extract_token_usage(response: Any) -> dict[str, int]:
        """Best-effort extraction of token usage from a LangChain LLMResult."""
        try:
            llm_output = getattr(response, "llm_output", None) or {}
            usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
            return {
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            }
        except Exception:  # noqa: BLE001
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    @staticmethod
    def _extract_model_name(response: Any) -> Optional[str]:
        try:
            llm_output = getattr(response, "llm_output", None) or {}
            return llm_output.get("model_name") or llm_output.get("model")
        except Exception:  # noqa: BLE001
            return None
