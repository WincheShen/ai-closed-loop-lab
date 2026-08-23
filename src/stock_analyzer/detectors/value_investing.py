"""价值：不追 20 日新高；PE 合理时给出回踩均线买点。"""
from __future__ import annotations

from src.stock_analyzer.detectors.types import (
    BarContext,
    DetectorHit,
    capped_stop,
    last_close,
    recent_high,
    recent_low,
    sma,
)

STRATEGY_ID = "value_investing"


def detect(ctx: BarContext) -> DetectorHit | None:
    bars = ctx.bars
    if len(bars) < 20:
        return None
    close = last_close(bars)
    high20 = recent_high(bars, 20)
    if close <= 0 or high20 <= 0:
        return None
    if close >= high20 * 0.98:
        return None
    ma20 = sma(bars, 20)
    if ma20 is None or close > ma20 * 1.08:
        return None
    if ctx.pe_ttm and (ctx.pe_ttm < 0 or ctx.pe_ttm > 40):
        return None
    entry = close
    stop = capped_stop(entry, min(recent_low(bars, 20), entry * 0.92), max_loss_pct=0.08)
    return DetectorHit(
        strategy_id=STRATEGY_ID,
        entry_price=round(entry, 2),
        stop_loss=round(stop, 2),
        target_price=round(entry * 1.20, 2),
        entry_condition="immediate",
        position_pct_cap=0.10,
        evidence={"pe_ttm": ctx.pe_ttm, "dist_high20": round((high20 - close) / high20, 3)},
    )
