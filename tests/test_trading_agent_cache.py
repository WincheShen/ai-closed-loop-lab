"""TradingAgent Cache 基本功能 smoke test。"""
from __future__ import annotations

from datetime import datetime

from trading_agent_service.analysis import MockAnalyzer
from trading_agent_service.cache import CacheManager


def test_store_and_lookup_hit(tmp_path):
    cache = CacheManager(tmp_path)
    analyzer = MockAnalyzer()
    report = analyzer.analyze("600519")

    cache.store(report, evaluated_at=datetime.now())

    result = cache.lookup("600519")
    assert result.hit is True
    assert result.report is not None
    assert result.report.symbol == "600519"


def test_lookup_miss_for_unknown(tmp_path):
    cache = CacheManager(tmp_path)
    result = cache.lookup("999999")
    assert result.hit is False
    assert "no valid cache" in result.reason


def test_price_out_of_range_invalidates(tmp_path):
    cache = CacheManager(tmp_path)
    analyzer = MockAnalyzer()
    report = analyzer.analyze("600519")
    cache.store(report, evaluated_at=datetime.now())

    low, high = report.reevaluation_price_range
    # 故意超出区间
    result = cache.lookup("600519", current_price=high * 2)
    assert result.hit is False
    assert "outside" in result.reason
