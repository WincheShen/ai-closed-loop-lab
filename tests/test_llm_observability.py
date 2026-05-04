"""Tests for the Phase 3.5 LLM observability skeleton.

These tests verify:
- the ambient request context (llm_request_context) is respected;
- the pricing book estimator works on default prices;
- an LLMUsageCallback can record a fake LLM response end-to-end into
  central_brain (no langchain needed — we call ``on_llm_end`` directly).
"""

from __future__ import annotations

import types

from src.central_brain.metadata_store import MemoryStore
from src.trading_agent_service.analysis.observability import (
    LLMUsageCallback,
    estimate_cost_usd,
    llm_request_context,
    current_request_id,
)


def test_llm_request_context_roundtrip():
    assert current_request_id() is None
    with llm_request_context(symbol="600519", stage="market") as rid:
        assert rid
        assert current_request_id() == rid
    assert current_request_id() is None


def test_estimate_cost_usd_with_default_pricing():
    cost = estimate_cost_usd(
        "gpt-4o-mini",
        prompt_tokens=1000,
        completion_tokens=1000,
    )
    # 1k prompt * 0.00015 + 1k completion * 0.00060 = 0.00075
    assert abs(cost - 0.00075) < 1e-9


def test_estimate_cost_usd_unknown_model_zero():
    assert estimate_cost_usd("some-unknown-model", 100, 100) == 0.0


def test_callback_records_to_central_brain(tmp_path, monkeypatch):
    # Isolate central_brain to a tmp sqlite
    store = MemoryStore(str(tmp_path / "obs.db"))

    class _FakeBrain:
        pass

    brain = _FakeBrain()
    brain.store = store

    # observability.py does ``from central_brain import get_central_brain``
    # at call time, so we patch the function on the central_brain package.
    import central_brain

    monkeypatch.setattr(central_brain, "get_central_brain", lambda: brain)

    # Build a fake LangChain-style response object
    response = types.SimpleNamespace(
        llm_output={
            "model_name": "gpt-4o-mini",
            "token_usage": {
                "prompt_tokens": 200,
                "completion_tokens": 100,
                "total_tokens": 300,
            },
        }
    )

    cb = LLMUsageCallback()

    with llm_request_context(symbol="600519", stage="market"):
        cb.on_llm_start({"name": "ChatOpenAI"}, prompts=["hi"], run_id="run-1")
        cb.on_llm_end(response, run_id="run-1")

    summary = store.llm_cost_summary()
    assert summary["total_calls"] == 1
    assert summary["total_tokens"] == 300
    assert summary["total_cost_usd"] > 0


def test_callback_records_error(tmp_path, monkeypatch):
    store = MemoryStore(str(tmp_path / "obs.db"))
    brain = types.SimpleNamespace(store=store)

    import central_brain

    monkeypatch.setattr(central_brain, "get_central_brain", lambda: brain)

    cb = LLMUsageCallback()
    with llm_request_context(symbol="600519", stage="market"):
        cb.on_llm_start({}, prompts=["x"], run_id="run-err")
        cb.on_llm_error(RuntimeError("rate limit"), run_id="run-err")

    summary = store.llm_cost_summary()
    assert summary["total_calls"] == 1
    assert summary["total_cost_usd"] == 0.0
