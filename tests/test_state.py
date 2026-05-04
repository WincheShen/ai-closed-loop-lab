"""Tests for TradingState and graph nodes."""

from __future__ import annotations

import pytest

from src.graph.state import StockCandidate, TradeSignal, create_empty_state


def test_create_empty_state() -> None:
    state = create_empty_state("test-session", "mock")
    assert state["session_id"] == "test-session"
    assert state["run_mode"] == "mock"
    assert state["target_stocks"] == []
    assert state["trade_signals"] == []
    assert state["filled_orders"] == []
    assert state["published_posts"] == []


def test_stock_candidate_structure() -> None:
    candidate: StockCandidate = {
        "symbol": "600519.SH",
        "name": "贵州茅台",
        "qlib_score": 0.92,
        "sector": "白酒",
        "hot_reason": ["消费复苏"],
        "kline_summary": {"trend": "up"},
        "fund_flow": None,
        "dragon_tiger": None,
    }
    assert candidate["symbol"] == "600519.SH"
    assert candidate["qlib_score"] > 0.9


def test_trade_signal_structure() -> None:
    signal: TradeSignal = {
        "signal_id": "SIG-TEST01",
        "symbol": "600519.SH",
        "action": "buy",
        "entry_price": 1500.0,
        "target_price": 1620.0,
        "stop_loss": 1425.0,
        "position_pct": 0.10,
        "strategy": "20日线回踩",
        "rationale": "测试",
        "timestamp": "2026-04-24T10:00:00",
        "expiry": "2026-04-29T10:00:00",
    }
    assert signal["action"] == "buy"
    assert signal["stop_loss"] < signal["entry_price"]
    assert signal["target_price"] > signal["entry_price"]
