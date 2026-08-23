"""SignalFactory 不采用 LLM 价格；检测器决定策略。"""
from __future__ import annotations

from datetime import date, timedelta

from src.agents.strategist.signal_factory import hits_to_signals
from src.stock_analyzer.data_source.akshare_client import KlineBar
from src.stock_analyzer.detectors.hot_sector_pullback import detect as detect_pullback
from src.stock_analyzer.detectors.types import BarContext


def test_hits_to_signals_keep_detector_prices() -> None:
    closes = [10 + i * 0.2 for i in range(16)] + [12.2, 12.0, 11.8, 11.6]
    d = date(2024, 1, 2)
    bars = []
    for i, c in enumerate(closes):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        prev = closes[i - 1] if i else c
        bars.append(
            KlineBar(date=d, open=prev, high=max(c, prev), low=min(c, prev),
                     close=c, volume=10000, turnover=1e8)
        )
        d += timedelta(days=1)
    hit = detect_pullback(BarContext("000001", bars, sector="半导体", hot_sectors=("半导体",)))
    assert hit is not None
    cand = {"symbol": "000001", "name": "x", "sector": "半导体"}
    sigs = hits_to_signals([(cand, hit)])
    assert sigs[0]["entry_price"] == hit.entry_price
    assert sigs[0]["stop_loss"] == hit.stop_loss
    assert sigs[0]["strategy_id"] == "hot_sector_pullback"
