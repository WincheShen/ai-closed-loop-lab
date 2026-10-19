"""market-information-platform 适配层单元测试。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from src.agents.cio.market_brain import MarketBrain
from src.stock_analyzer.data_source.akshare_client import (
    AkshareClient,
    KlineBar,
    MarketSnapshot,
    SectorQuote,
    StockQuote,
)
from src.stock_analyzer.data_source.market_info_client import (
    MarketInfoClient,
    _CircuitBreaker,
)


@dataclass
class FakeResp:
    data: Any
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self) -> Any:
        return self.data


def _make_snapshot_json(stocks: list[dict], sectors: list[dict]) -> dict:
    return {
        "source": "mock",
        "data_quality": "good",
        "fetched_at": "2026-08-25T10:00:00",
        "is_mock": True,
        "items": stocks,
        "sectors": sectors,
    }


def _make_kline_json(bars: list[dict]) -> dict:
    return {
        "source": "mock",
        "data_quality": "good",
        "fetched_at": "2026-08-25T10:00:00",
        "is_mock": True,
        "symbol": "600519",
        "period": "daily",
        "bars": bars,
    }


def _snapshot_get(stocks: list[dict], sectors: list[dict]):
    snapshot = _make_snapshot_json(stocks, sectors)

    def _get(url: str, *args, **kwargs):
        if "/api/market/snapshot" in url:
            return FakeResp(snapshot)
        if "/api/market/sectors" in url:
            return FakeResp({"source": "mock", "data_quality": "good", "fetched_at": "2026-08-25T10:00:00", "is_mock": True, "sectors": sectors})
        raise RuntimeError(f"unexpected URL: {url}")

    return _get


def _kline_get(bars: list[dict]):
    kline = _make_kline_json(bars)

    def _get(url: str, *args, **kwargs):
        if "/api/market/kline" in url:
            return FakeResp(kline)
        raise RuntimeError(f"unexpected URL: {url}")

    return _get


def test_market_info_platform_success(monkeypatch):
    """平台成功：snapshot/kline 均映射为现有类型并保留 source/data_quality/is_mock。"""
    monkeypatch.setenv("MARKET_INFO_URL", "http://localhost:8002")
    client = AkshareClient(allow_mock_fallback=True)
    client._ak_available = False

    stocks = [
        {"symbol": "600519", "name": "贵州茅台", "price": 1700.0, "change_pct": 1.2, "volume": 1000.0, "amount": 1e9},
        {"symbol": "000001", "name": "平安银行", "price": 12.5, "change_pct": -0.5, "volume": 5000.0, "amount": 6.25e7},
    ]
    sectors = [
        {"sector_name": "白酒", "change_pct": 2.1, "top_stock": "600519", "stock_count": 5},
    ]
    monkeypatch.setattr(
        client._market_info_client._session, "get", _snapshot_get(stocks, sectors)
    )

    snap = client.fetch_snapshot()
    assert isinstance(snap, MarketSnapshot)
    assert snap.source == "mock"
    assert snap.data_quality == "full"
    assert snap.is_mock is True
    assert len(snap.stocks) == 2
    assert snap.stocks[0].symbol == "600519"
    assert snap.stocks[0].turnover == 1e9
    assert len(snap.sectors) == 1
    assert snap.sectors[0].name == "白酒"
    assert snap.sectors[0].leading_stocks == ["600519"]

    bars = [
        {"trade_date": "2026-08-24", "open": 1690.0, "high": 1705.0, "low": 1685.0, "close": 1700.0, "volume": 1000.0},
        {"trade_date": "2026-08-25", "open": 1700.0, "high": 1710.0, "low": 1698.0, "close": 1710.0, "volume": 1200.0},
    ]
    monkeypatch.setattr(
        client._market_info_client._session, "get", _kline_get(bars)
    )
    kline = client.fetch_kline("600519", days=2)
    assert len(kline) == 2
    assert kline[-1].close == 1710.0
    assert all(isinstance(b, KlineBar) for b in kline)
    assert kline[0].source == "mock"
    assert kline[0].data_quality == "good"
    assert kline[0].is_mock is True


def test_market_info_no_sector(monkeypatch):
    """平台 snapshot 无板块数据时仍返回空 sectors 并保留元数据。"""
    monkeypatch.setenv("MARKET_INFO_URL", "http://localhost:8002")
    client = AkshareClient(allow_mock_fallback=True)
    client._ak_available = False

    stocks = [
        {"symbol": "600519", "name": "贵州茅台", "price": 1700.0, "change_pct": 1.2, "volume": 1000.0, "amount": 1e9},
    ]
    monkeypatch.setattr(
        client._market_info_client._session, "get", _snapshot_get(stocks, [])
    )

    snap = client.fetch_snapshot()
    assert snap.sectors == []
    assert snap.source == "mock"
    assert snap.data_quality == "no_sector"
    assert snap.stocks[0].symbol == "600519"


def test_market_info_unreachable_fallback(monkeypatch):
    """平台不可达时安全回退现有 AkshareClient（最终 mock）。"""
    monkeypatch.setenv("MARKET_INFO_URL", "http://localhost:8002")
    client = AkshareClient(allow_mock_fallback=True)
    client._ak_available = False

    def raise_unreachable(*args, **kwargs):
        raise requests.ConnectionError("unreachable")

    monkeypatch.setattr(
        client._market_info_client._session, "get", raise_unreachable
    )

    def raise_sina(*args, **kwargs):
        raise RuntimeError("sina down")

    monkeypatch.setattr(client, "_fetch_sina", raise_sina)

    fallback = MarketSnapshot(
        snapshot_date=date.today(),
        stocks=[StockQuote(symbol="000001", name="", price=1.0, change_pct=0.0, volume=0.0, turnover=0.0)],
        sectors=[SectorQuote(name="测试", change_pct=0.0, turnover=0.0)],
        is_mock=True,
        data_quality="mock",
        source="mock",
    )
    monkeypatch.setattr(client, "_fetch_mock", lambda: fallback)

    snap = client.fetch_snapshot()
    assert snap is fallback
    assert snap.is_mock is True


def test_force_defend_overrides_posture_and_position(monkeypatch):
    """approved force_defend 使 MarketBrain posture=defend、max_position=0 并记录来源；disable_source 不重复执行。"""
    monkeypatch.setenv("MARKET_INFO_URL", "http://localhost:8002")
    client = AkshareClient(allow_mock_fallback=True)
    client._ak_available = False

    def fake_get(url: str, *args, **kwargs):
        if "/api/intervention/active" in url:
            return FakeResp([
                {"id": 1, "type": "disable_source", "params": {"source": "akshare"}, "reason": "disable", "status": "approved", "submitted_by": "ops", "submitted_at": "2026-08-25T00:00:00", "reviewed_by": "admin", "reviewed_at": "2026-08-25T01:00:00", "is_active": True, "effective_until": None},
                {"id": 2, "type": "force_defend", "params": {}, "reason": "circuit break", "status": "approved", "submitted_by": "ops", "submitted_at": "2026-08-25T00:00:00", "reviewed_by": "admin", "reviewed_at": "2026-08-25T01:00:00", "is_active": True, "effective_until": None},
            ])
        if "/api/market/snapshot" in url:
            return FakeResp({
                "source": "mock",
                "data_quality": "good",
                "fetched_at": "2026-08-25T10:00:00",
                "is_mock": False,
                "items": [{"symbol": "000001", "name": "平安银行", "price": 12.0, "change_pct": 0.1, "volume": 100.0, "amount": 1200.0}],
            })
        if "/api/market/sectors" in url:
            return FakeResp({"source": "mock", "data_quality": "good", "fetched_at": "2026-08-25T10:00:00", "is_mock": False, "sectors": []})
        raise RuntimeError(f"unexpected URL: {url}")

    monkeypatch.setattr(client._market_info_client._session, "get", fake_get)

    brain = MarketBrain.__new__(MarketBrain)
    brain.session_id = "s1"
    brain.logger = MagicMock()
    brain.persona = MagicMock(
        persona_version="v1",
        max_total_position_for=lambda r: 0.8,
        prompt_summary=lambda: "",  # not used in this path
    )
    brain.brain = MagicMock()
    brain.brain.store = MagicMock()
    brain.akshare = client
    brain.hot_detector = MagicMock()
    brain.hot_detector.detect = MagicMock(return_value=[])
    brain.emappdata_detector = MagicMock()
    brain.emappdata_detector.detect = MagicMock(return_value=[])

    # 避免网络/LLM/DB
    monkeypatch.setattr(
        brain,
        "_llm_judge",
        lambda *a, **k: {
            "regime": "bull",
            "risk_appetite": "high",
            "recommended_posture": "attack",
            "dominant_styles": [],
            "avoid_styles": [],
            "strategy_bias": {},
            "daily_questions": [],
            "summary": "bull attack",
        },
    )
    monkeypatch.setattr(brain, "_recent_performance_block", lambda: "none")
    monkeypatch.setattr(brain, "_load_last_real_regime", lambda: None)

    result = brain.generate_snapshot()
    assert result.recommended_posture == "defend"
    assert result.max_total_position_pct == 0.0
    assert "force_defend" in result.evidence
    assert result.evidence["force_defend"]["source"] == "market_info_platform"
    assert result.evidence["force_defend"]["type"] == "force_defend"
    # disable_source 不应导致额外动作，result 不是 disable 触发
    assert result.evidence["force_defend"]["type"] != "disable_source"


def test_circuit_breaker_opens_after_threshold():
    """连续 3 次失败后 circuit breaker 打开，快速跳过而非等 15s 超时。"""
    cb = _CircuitBreaker(threshold=3, recovery_seconds=60)
    assert cb.can_attempt()
    cb.record_failure()
    cb.record_failure()
    assert cb.can_attempt()  # 2 次未到阈值
    cb.record_failure()
    assert not cb.can_attempt()  # 3 次到阈值
    assert cb.is_open
    cb.record_success()
    assert cb.can_attempt()  # reset
    assert not cb.is_open


def test_circuit_breaker_in_client(monkeypatch):
    """MarketInfoClient 连续失败后 circuit breaker 快速拒绝。"""
    client = MarketInfoClient(base_url="http://localhost:9999", timeout=1)

    def raise_err(*args, **kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(client._session, "get", raise_err)

    # 前 3 次调用实际发请求（并失败）
    for _ in range(3):
        with pytest.raises(requests.ConnectionError):
            client._get("/api/health")

    # 第 4 次直接被 circuit breaker 拒绝，不再等超时
    with pytest.raises(ConnectionError, match="circuit breaker OPEN"):
        client._get("/api/health")


def test_intraday_client_reads_from_market_info_db(monkeypatch):
    """IntradayClient 在配置 MARKET_INFO_URL 时直接从行情平台数据库读取快照与真实昨收。"""
    from src.stock_analyzer.data_source.intraday_client import IntradayClient

    monkeypatch.setenv("MARKET_INFO_URL", "http://localhost:8005")
    intraday = IntradayClient(allow_mock_fallback=False)

    quote_resp = {
        "source": "sina",
        "data_quality": "full",
        "fetched_at": "2026-10-19T06:35:43+00:00",
        "is_mock": False,
        "items": [
            {
                "symbol": "002463",
                "name": "沪电股份",
                "price": 113.18,
                "change_pct": -0.18,
                "volume": 418336.0,
                "amount": 4627009855.0,
                "open": 112.20,
                "high": 114.18,
                "low": 106.11,
                "prev_close": 113.38,
                "turnover_rate": 2.45,
            }
        ],
    }

    def fake_get(url: str, *args, **kwargs):
        if "/api/market/snapshot?symbols=002463" in url:
            return FakeResp(quote_resp)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(intraday._market_info_client._session, "get", fake_get)

    snap = intraday.fetch_intraday_snapshot("002463", name="沪电股份")
    assert intraday._last_source == "market_info"
    assert snap.is_mock is False
    assert snap.current_price == 113.18
    assert snap.prev_close == 113.38
    assert snap.change_pct == -0.18
    assert snap.high == 114.18
    assert snap.low == 106.11
    assert snap.open == 112.20
    assert snap.turnover == 4627009855.0
    assert len(snap.bars) == 2
    assert sum(b.turnover for b in snap.bars) == 4627009855.0

