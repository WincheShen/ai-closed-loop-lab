"""SignalFactory：价格来自检测器；无 hit 零次 LLM。"""
from __future__ import annotations

from datetime import date, timedelta

from src.agents.strategist.signal_factory import (
    detect_hits,
    hits_to_signals,
    review_hits,
    run_factory,
)
from src.stock_analyzer.data_source.akshare_client import KlineBar
from src.stock_analyzer.detectors.hot_sector_pullback import detect as detect_pullback
from src.stock_analyzer.detectors.types import BarContext


class _Persona:
    strategy_regime_compatibility = {"hot_sector_pullback": {"compatible": ["bull"]}}


def _pullback_bars() -> list[KlineBar]:
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
    return bars


def _candidate(bars: list[KlineBar]) -> dict:
    return {
        "symbol": "000001",
        "name": "x",
        "sector": "半导体",
        "kline_summary": {
            "pe_ttm": 18,
            "bars": [
                {
                    "date": str(b.date), "open": b.open, "high": b.high,
                    "low": b.low, "close": b.close, "volume": b.volume,
                    "turnover": b.turnover,
                }
                for b in bars
            ],
        },
    }


def test_hits_to_signals_keep_detector_prices() -> None:
    bars = _pullback_bars()
    hit = detect_pullback(BarContext("000001", bars, sector="半导体", hot_sectors=("半导体",)))
    assert hit is not None
    cand = {"symbol": "000001", "name": "x", "sector": "半导体"}
    sigs = hits_to_signals([(cand, hit)])
    assert sigs[0]["entry_price"] == hit.entry_price
    assert sigs[0]["stop_loss"] == hit.stop_loss
    assert sigs[0]["strategy_id"] == "hot_sector_pullback"


def test_llm_cannot_override_price() -> None:
    bars = _pullback_bars()
    hit = detect_pullback(BarContext("000001", bars, sector="半导体", hot_sectors=("半导体",)))
    assert hit is not None
    cand = {"symbol": "000001", "name": "x", "sector": "半导体"}

    def fake_llm(_msgs):
        return type("R", (), {"content": '{"decision":"TAKE","confidence":0.9,"rationale":"放量突破","entry_price":999,"strategy":"volume_breakout"}'})()

    sigs = review_hits([(cand, hit)], market_regime={"regime": "bull"}, llm_invoke=fake_llm)
    assert len(sigs) == 1
    assert sigs[0]["entry_price"] == hit.entry_price
    assert sigs[0]["strategy_id"] == "hot_sector_pullback"
    assert sigs[0]["entry_price"] != 999


def test_empty_hits_zero_llm_calls() -> None:
    called = {"n": 0}

    def boom(_msgs):
        called["n"] += 1
        raise AssertionError("should not call llm")

    hits, sigs = run_factory(
        [{"symbol": "1", "kline_summary": {"bars": []}}],
        persona=_Persona(),
        market_regime={"regime": "bull", "hot_sectors": ["半导体"]},
        llm_invoke=boom,
    )
    assert hits == []
    assert sigs == []
    assert called["n"] == 0


def test_detect_hits_from_candidate_bars() -> None:
    cand = _candidate(_pullback_bars())
    hits = detect_hits(
        [cand],
        persona=_Persona(),
        market_regime={"regime": "bull", "data_quality": "full"},
        hot_sectors=["半导体"],
    )
    assert hits
    assert hits[0][1].strategy_id == "hot_sector_pullback"


# --- ultra_short_momentum -------------------------------------------------

class _MomPersona:
    strategy_regime_compatibility = {
        "ultra_short_momentum": {"compatible": ["bull", "neutral", "rebound"]}
    }


def _mom_bars() -> list[KlineBar]:
    """18 日慢涨 + 3 日放量急涨收高位。"""
    specs = []
    for i in range(18):
        c = 20 * (1 + 0.008 * i)
        o = c * 0.997
        specs.append((o, c * 1.01, o * 0.99, c, 4e8))
    base = specs[-1][3]
    for step in range(3):
        o = base * (1 + 0.035 * step)
        c = base * (1 + 0.035 * (step + 1))
        specs.append((o, c * 1.03, o * 0.965, c, 6e8))
    d = date(2024, 1, 2)
    bars = []
    for o, h, lo, c, to in specs:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        bars.append(
            KlineBar(date=d, open=o, high=h, low=lo, close=c, volume=3e4, turnover=to)
        )
        d += timedelta(days=1)
    return bars


def test_momentum_persona_routes_to_detector() -> None:
    """人格 compat 只声明 ultra_short_momentum → 只走该检测器。"""
    cand = _candidate(_mom_bars())  # symbol 000001 在深市主板允许前缀内
    hits = detect_hits(
        [cand],
        persona=_MomPersona(),
        market_regime={"regime": "bull", "data_quality": "full"},
    )
    assert hits
    assert hits[0][1].strategy_id == "ultra_short_momentum"
    assert hits[0][1].evidence["setup"] == "momentum_continuation"


def test_momentum_breadth_gate_from_regime_evidence() -> None:
    """market_regime.evidence.up_ratio 传入 BarContext，宽度差时无 hit。"""
    cand = _candidate(_mom_bars())
    base = {"regime": "bull", "data_quality": "full"}
    hits = detect_hits(
        [cand], persona=_MomPersona(),
        market_regime={**base, "evidence": {"up_ratio": 0.6}},
    )
    assert hits
    hits = detect_hits(
        [cand], persona=_MomPersona(),
        market_regime={**base, "evidence": {"up_ratio": 0.2}},
    )
    assert hits == []
