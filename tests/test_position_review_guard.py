"""持仓复审数据防护 — 2026-09-22 mock 数据误杀事故回归测试。

事故链: akshare/新浪全挂 → 降级 mock → is_mock 误标 False →
reviewer 无防护 → 25.07 假价触发 600519 止损强平。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from src.agents.reviewer.position_reviewer import PositionReviewAgent
from src.stock_analyzer.data_source.intraday_client import (
    IntradayClient,
    IntradaySnapshot,
    MinuteBar,
)


def _raise(*_args, **_kwargs):
    raise RuntimeError("source down")


def _bars(price: float, n: int = 4) -> list[MinuteBar]:
    return [
        MinuteBar(
            timestamp=datetime(2026, 9, 22, 10 + i, 0),
            open=price, high=price * 1.01, low=price * 0.99,
            close=price, volume=100, turnover=1e6,
        )
        for i in range(n)
    ]


# ── IntradayClient 数据源标记 ────────────────────────────────

def test_snapshot_marks_mock_when_all_real_sources_fail(monkeypatch):
    """akshare 已安装但请求失败 + 新浪失败 → mock 必须标 is_mock=True。"""
    client = IntradayClient(allow_mock_fallback=True)
    monkeypatch.setattr(client, "_fetch_minute_real", _raise)
    monkeypatch.setattr(client, "_fetch_minute_sina", _raise)

    snap = client.fetch_intraday_snapshot("600519")
    assert client._last_source == "mock"
    assert snap.is_mock is True


def test_snapshot_not_mock_on_sina_fallback(monkeypatch):
    """东财失败但新浪成功 → 真实数据，is_mock=False。"""
    client = IntradayClient(allow_mock_fallback=True)
    monkeypatch.setattr(client, "_fetch_minute_real", _raise)
    monkeypatch.setattr(client, "_fetch_minute_sina", lambda *a, **k: _bars(1255.0))

    snap = client.fetch_intraday_snapshot("600519")
    assert client._last_source == "sina"
    assert snap.is_mock is False
    assert snap.current_price == pytest.approx(1255.0)


def test_snapshot_not_mock_on_primary_source(monkeypatch):
    client = IntradayClient(allow_mock_fallback=True)
    monkeypatch.setattr(client, "_fetch_minute_real", lambda *a, **k: _bars(1255.0))

    snap = client.fetch_intraday_snapshot("600519")
    assert client._last_source == "eastmoney"
    assert snap.is_mock is False


# ── PositionReviewAgent 防护 ─────────────────────────────────

class _StubStore:
    def __init__(self, reviews=None):
        self.reviews = reviews or []
        self.saved = []

    def list_position_reviews(self, position_id, limit=3):
        return self.reviews[:limit]

    def save_position_review(self, **kwargs):
        self.saved.append(kwargs)

    def update_position_review(self, position_id, action, reason):
        pass


class _StubBrain:
    def __init__(self, reviews=None):
        self.store = _StubStore(reviews)


class _StubIntraday:
    def __init__(self, snapshot):
        self._snapshot = snapshot
        self._last_source = "mock" if snapshot.is_mock else "eastmoney"

    def fetch_intraday_snapshot(self, symbol, name="", period="30", bar_limit=16):
        return self._snapshot


def _snapshot(price: float, is_mock: bool) -> IntradaySnapshot:
    bars = _bars(price)
    return IntradaySnapshot(
        symbol="600519", name="贵州茅台", current_price=price,
        prev_close=price, change_pct=0.0,
        high=price * 1.01, low=price * 0.99, open=price,
        volume=100, turnover=1e6, bars=bars, is_mock=is_mock,
    )


def _agent(snapshot, reviews=None) -> PositionReviewAgent:
    agent = PositionReviewAgent.__new__(PositionReviewAgent)
    agent.intraday = _StubIntraday(snapshot)
    agent.brain = _StubBrain(reviews)
    agent.model_name = None
    agent.persona_id = None
    return agent


_POSITION = {
    "position_id": "POS-C7778B74",
    "symbol": "600519",
    "name": "贵州茅台",
    "entry_price": 1252.78,
    "current_qty": 100,
    "stop_loss": 1003.20,
    "target_price": 1500.0,
    "side": "long",
}


def test_mock_snapshot_never_triggers_exit():
    """复刻事故: mock 价 25.07 ≤ 止损 1003.20 → 必须 HOLD 而不是 EXIT。"""
    agent = _agent(_snapshot(25.07, is_mock=True))
    result = agent.review_position(dict(_POSITION))

    assert result["action"] == "HOLD"
    assert result["risk_flag"] == "DATA_ANOMALY"
    assert result.get("data_guard") is True
    assert "25.07" in result["reason"]


def test_deviant_price_skipped_against_last_review():
    """真实源但价格偏离上次有效价 >30% → 跳过，不误杀。"""
    reviews = [{"current_price": 1255.0}]
    agent = _agent(_snapshot(25.07, is_mock=False), reviews=reviews)
    result = agent.review_position(dict(_POSITION))

    assert result["action"] == "HOLD"
    assert result["data_guard"] is True
    assert "疑似行情数据异常" in result["reason"]


def test_normal_price_passes_guard(monkeypatch):
    """正常价格不应触发防护 — 继续走后续流程（此处让 LLM 失败收尾）。"""
    class _FailLLM:
        def invoke(self, _messages):
            raise RuntimeError("no llm in test")

    agent = _agent(_snapshot(1248.0, is_mock=False))
    monkeypatch.setattr(
        "src.agents.reviewer.position_reviewer.get_llm",
        lambda model_name=None: _FailLLM(),
    )
    result = agent.review_position(dict(_POSITION))

    assert "data_guard" not in result
    assert result["action"] == "HOLD"  # LLM 失败的兜底 HOLD
    assert "LLM调用失败" in result["reason"]
