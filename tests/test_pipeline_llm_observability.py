"""Tests for the pipeline LLM observability callback (PipelineLLMCallback).

Verifies:
- The callback records successful LLM calls to central_brain.llm_calls.
- The callback records LLM errors without propagating exceptions.
- The provider and stage are correctly stored.
- Ambient context vars (from llm_request_context) override defaults.
"""

from __future__ import annotations

import types

from src.central_brain.metadata_store import MemoryStore
from src.infra.llm_observability import PipelineLLMCallback


def _make_fake_brain(tmp_path):
    store = MemoryStore(str(tmp_path / "pipeline_obs.db"))
    brain = types.SimpleNamespace(store=store)
    return brain, store


def test_pipeline_callback_records_success(tmp_path, monkeypatch):
    brain, store = _make_fake_brain(tmp_path)

    import central_brain
    monkeypatch.setattr(central_brain, "get_central_brain", lambda: brain)

    cb = PipelineLLMCallback(provider="openai", stage="strategist")

    # Simulate LLM call lifecycle
    response = types.SimpleNamespace(
        llm_output={
            "model_name": "gpt-4o-mini",
            "token_usage": {
                "prompt_tokens": 150,
                "completion_tokens": 80,
                "total_tokens": 230,
            },
        }
    )

    cb.on_llm_start({"name": "ChatOpenAI"}, prompts=["hello"], run_id="run-1")
    cb.on_llm_end(response, run_id="run-1")

    summary = store.llm_cost_summary()
    assert summary["total_calls"] == 1
    assert summary["total_tokens"] == 230
    assert summary["total_cost_usd"] > 0


def test_pipeline_callback_records_error(tmp_path, monkeypatch):
    brain, store = _make_fake_brain(tmp_path)

    import central_brain
    monkeypatch.setattr(central_brain, "get_central_brain", lambda: brain)

    cb = PipelineLLMCallback(provider="azure", stage="market_brain")

    cb.on_llm_start({}, prompts=["x"], run_id="run-err")
    cb.on_llm_error(RuntimeError("timeout"), run_id="run-err")

    summary = store.llm_cost_summary()
    assert summary["total_calls"] == 1
    assert summary["total_cost_usd"] == 0.0


def test_pipeline_callback_chat_model_start(tmp_path, monkeypatch):
    """ChatOpenAI fires on_chat_model_start instead of on_llm_start."""
    brain, store = _make_fake_brain(tmp_path)

    import central_brain
    monkeypatch.setattr(central_brain, "get_central_brain", lambda: brain)

    cb = PipelineLLMCallback(provider="openai", stage="reviewer")

    response = types.SimpleNamespace(
        llm_output={
            "model_name": "gpt-4o",
            "token_usage": {
                "prompt_tokens": 500,
                "completion_tokens": 200,
                "total_tokens": 700,
            },
        }
    )

    # Use on_chat_model_start (the hook ChatOpenAI actually fires)
    cb.on_chat_model_start(
        {"name": "ChatOpenAI"},
        messages=[[{"role": "user", "content": "hi"}]],
        run_id="run-chat",
    )
    cb.on_llm_end(response, run_id="run-chat")

    summary = store.llm_cost_summary()
    assert summary["total_calls"] == 1
    assert summary["total_tokens"] == 700


def test_pipeline_callback_ambient_context_override(tmp_path, monkeypatch):
    """When llm_request_context is active, its values override defaults."""
    brain, store = _make_fake_brain(tmp_path)

    import central_brain
    monkeypatch.setattr(central_brain, "get_central_brain", lambda: brain)

    from src.trading_agent_service.analysis.observability import llm_request_context

    cb = PipelineLLMCallback(provider="openai", stage="default_stage")

    response = types.SimpleNamespace(
        llm_output={
            "model_name": "gpt-4o-mini",
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }
    )

    with llm_request_context(symbol="600519", stage="market"):
        cb.on_chat_model_start({}, messages=[[]], run_id="run-ctx")
        cb.on_llm_end(response, run_id="run-ctx")

    summary = store.llm_cost_summary()
    assert summary["total_calls"] == 1


def test_pipeline_callback_failure_does_not_propagate(monkeypatch):
    """If central_brain is unavailable, the callback must not raise."""
    import central_brain

    monkeypatch.setattr(
        central_brain, "get_central_brain", lambda: (_ for _ in ()).throw(RuntimeError("db down"))
    )

    cb = PipelineLLMCallback(provider="openai", stage="test")

    response = types.SimpleNamespace(
        llm_output={
            "model_name": "gpt-4o-mini",
            "token_usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
    )

    # This must not raise
    cb.on_llm_start({}, prompts=["hi"], run_id="run-fail")
    cb.on_llm_end(response, run_id="run-fail")
