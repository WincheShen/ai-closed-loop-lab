"""热点板块前排回踩：板块在热点 + 距 20 日高点回撤 3%~12% + 量能不萎缩。"""
from __future__ import annotations

from src.stock_analyzer.detectors.types import (
    BarContext,
    DetectorHit,
    capped_stop,
    last_close,
    recent_high,
    sma,
)

STRATEGY_ID = "hot_sector_pullback"


def detect(ctx: BarContext) -> DetectorHit | None:
    if not ctx.hot_sectors or ctx.sector not in ctx.hot_sectors:
        return None
    bars = ctx.bars
    if len(bars) < 20:
        return None
    close = last_close(bars)
    high20 = recent_high(bars, 20)
    if close <= 0 or high20 <= 0:
        return None
    pullback = (high20 - close) / high20
    if pullback < 0.03 or pullback > 0.12:
        return None
    vol_ma = sma(bars, 20)
    # 用成交额近似量能；KlineBar.turnover 为元
    last_to = bars[-1].turnover or 0
    avg_to = (
        sum(b.turnover for b in bars[-20:]) / 20.0
        if all(b.turnover for b in bars[-20:])
        else 0
    )
    if avg_to > 0 and last_to < avg_to * 0.8:
        return None
    entry = close
    from src.stock_analyzer.detectors.types import recent_low
    stop = capped_stop(entry, recent_low(bars, 10))
    target = round(entry * 1.10, 2)
    return DetectorHit(
        strategy_id=STRATEGY_ID,
        entry_price=round(entry, 2),
        stop_loss=round(stop, 2),
        target_price=target,
        entry_condition="pullback",
        position_pct_cap=0.06,
        evidence={
            "pullback_pct": round(pullback * 100, 2),
            "high20": high20,
            "vol_ok": True,
            "ma20": vol_ma,
            "sector": ctx.sector,
        },
    )
