"""底部启动：近 N 日累计下跌 + 当日阳线放量 + MA5 上穿 MA10。"""
from __future__ import annotations

from src.stock_analyzer.detectors.types import (
    BarContext,
    DetectorHit,
    capped_stop,
    last_close,
    recent_low,
    sma,
)

STRATEGY_ID = "bottom_reversal"


def detect(ctx: BarContext) -> DetectorHit | None:
    bars = ctx.bars
    if len(bars) < 12:
        return None
    window = bars[-6:-1]
    if not window:
        return None
    drop = (window[0].close - window[-1].close) / window[0].close if window[0].close else 0
    if drop < 0.06:
        return None
    last = bars[-1]
    if last.close <= last.open:
        return None
    avg_vol = sum(b.volume for b in bars[-6:-1]) / 5.0
    if avg_vol > 0 and last.volume < avg_vol * 1.2:
        return None
    ma5 = sma(bars, 5)
    ma10 = sma(bars, 10)
    prev_ma5 = sma(bars[:-1], 5)
    prev_ma10 = sma(bars[:-1], 10)
    if None in (ma5, ma10, prev_ma5, prev_ma10):
        return None
    if not (prev_ma5 <= prev_ma10 and ma5 > ma10):
        return None
    entry = last_close(bars)
    stop = capped_stop(entry, recent_low(bars, 10))
    return DetectorHit(
        strategy_id=STRATEGY_ID,
        entry_price=round(entry, 2),
        stop_loss=round(stop, 2),
        target_price=round(entry * 1.12, 2),
        entry_condition="immediate",
        position_pct_cap=0.05,
        evidence={"drop_pct": round(drop * 100, 2), "ma5": ma5, "ma10": ma10},
    )
