"""LLM usage observability — Phase 3.5 S1.

Provides:
- ``LLMUsageCallback``: LangChain ``BaseCallbackHandler`` that writes every
  LLM call to ``central_brain.llm_calls``.
- ``llm_request_context``: context-manager setting the ambient request_id /
  symbol / stage so the callback can tag each call.
- ``estimate_cost_usd``: price-book lookup, pluggable via
  ``config/llm_pricing.yaml``.

Design notes:
- LangChain is an **optional** dependency. Importing this module must not
  fail when langchain is not installed; in that case ``LLMUsageCallback``
  falls back to an empty stub.
- The real adapter (``RealTradingAgentsAdapter``) is expected to import and
  attach the callback to its ``ChatOpenAI`` client once langchain is
  available. This hook is intentionally left for Phase 3.5 S1 wiring.
- The DB write is best-effort — failure never propagates back into the LLM
  call path.
"""

from __future__ import annotations

import contextvars
import logging
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ambient request context (populated by adapter before propagate())
# ---------------------------------------------------------------------------

_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_llm_request_id", default=None
)
_symbol: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_llm_symbol", default=None
)
_stage: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_llm_stage", default=None
)


@contextmanager
def llm_request_context(
    request_id: Optional[str] = None,
    symbol: Optional[str] = None,
    stage: Optional[str] = None,
) -> Iterator[str]:
    """Bind the ambient request_id/symbol/stage for nested LLM calls.

    ``request_id`` is auto-generated if not provided and is returned so the
    caller can log or reuse it.
    """
    rid = request_id or str(uuid.uuid4())
    t_rid = _request_id.set(rid)
    t_sym = _symbol.set(symbol)
    t_stg = _stage.set(stage)
    try:
        yield rid
    finally:
        _request_id.reset(t_rid)
        _symbol.reset(t_sym)
        _stage.reset(t_stg)


def current_request_id() -> Optional[str]:
    return _request_id.get()


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

_DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # Fallback numbers in case config/llm_pricing.yaml is missing.
    # Actual contract price should be put in that file.
    "gpt-4o-mini": {"prompt_per_1k_usd": 0.00015, "completion_per_1k_usd": 0.00060},
    "gpt-4o": {"prompt_per_1k_usd": 0.0025, "completion_per_1k_usd": 0.010},
    "gpt-5.3-chat": {"prompt_per_1k_usd": 0.005, "completion_per_1k_usd": 0.015},
    "deepseek-chat": {"prompt_per_1k_usd": 0.00014, "completion_per_1k_usd": 0.00028},
}


def _load_pricing() -> dict[str, dict[str, float]]:
    """Load pricing from config/llm_pricing.yaml if present."""
    try:
        import yaml  # type: ignore
    except Exception:
        return _DEFAULT_PRICING

    path = Path("config/llm_pricing.yaml")
    if not path.exists():
        return _DEFAULT_PRICING
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        models = data.get("models") or {}
        if not isinstance(models, dict):
            return _DEFAULT_PRICING
        merged = dict(_DEFAULT_PRICING)
        for name, entry in models.items():
            if isinstance(entry, dict):
                merged[name] = entry
        return merged
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to read llm_pricing.yaml: %s", e)
        return _DEFAULT_PRICING


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return USD cost estimate based on the pricing book."""
    pricing = _load_pricing()
    entry = pricing.get(model) or {}
    p_rate = float(entry.get("prompt_per_1k_usd", 0.0))
    c_rate = float(entry.get("completion_per_1k_usd", 0.0))
    return round(
        (prompt_tokens * p_rate / 1000.0) + (completion_tokens * c_rate / 1000.0),
        6,
    )


# ---------------------------------------------------------------------------
# LangChain callback (optional dependency)
# ---------------------------------------------------------------------------

try:  # pragma: no cover - import guard
    from langchain_core.callbacks.base import BaseCallbackHandler  # type: ignore

    _HAS_LANGCHAIN = True
except Exception:  # noqa: BLE001
    BaseCallbackHandler = object  # type: ignore[misc,assignment]
    _HAS_LANGCHAIN = False


class LLMUsageCallback(BaseCallbackHandler):  # type: ignore[misc]
    """Records each LLM call into central_brain.llm_calls.

    Safe to attach even when langchain is missing — the methods below simply
    never fire in that case.
    """

    def __init__(self) -> None:
        # map run_id (LangChain's) → start time so we can compute latency
        self._starts: dict[Any, float] = {}

    # -- LangChain hooks ------------------------------------------------

    def on_llm_start(self, serialized: dict, prompts: list, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        if run_id is not None:
            self._starts[run_id] = time.time()

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        started = self._starts.pop(run_id, None)
        latency_ms = int((time.time() - started) * 1000) if started else None

        usage = _extract_token_usage(response)
        model = _extract_model_name(response) or "unknown"

        cost = estimate_cost_usd(
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

    # -- Internal -------------------------------------------------------

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

            get_central_brain().store.record_llm_call(
                request_id=_request_id.get() or "unknown",
                symbol=_symbol.get(),
                stage=_stage.get(),
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                success=success,
                error_msg=error_msg,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("LLMUsageCallback record failed: %s", e)


# ---------------------------------------------------------------------------
# Response parsing helpers
# ---------------------------------------------------------------------------

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


def _extract_model_name(response: Any) -> Optional[str]:
    try:
        llm_output = getattr(response, "llm_output", None) or {}
        return llm_output.get("model_name") or llm_output.get("model")
    except Exception:  # noqa: BLE001
        return None
