"""底部启动：先跌出底，再放量阳线 + MA5 上穿 MA10 确认。

v2（2026-10）定义修正：v1 要求「前 4 日跌 ≥6%」与「当日 MA5 上穿 MA10」同日成立，
数学上互斥（连跌后 MA5 远低于 MA10，单日需涨 ≥11.5% 才能金叉，超出主板涨停），
2024 至今 79 只全缓存池 0 次命中。v2 参数不变，只把下跌与金叉在时间上解耦：
- 近 DROP_LOOKBACK 日内出现 ≥ MIN_DROP 的高点→低点回撤
- 低点落在最近 BOTTOM_WITHIN 日内（底部刚出现，不追已走完的反弹）
- 当日收阳、放量 ≥ VOL_RATIO × 前 5 日均量、MA5 上穿 MA10
"""
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

DROP_LOOKBACK = 15
BOTTOM_WITHIN = 10
MIN_DROP = 0.06
VOL_RATIO = 1.2


def detect(ctx: BarContext) -> DetectorHit | None:
    bars = ctx.bars
    if len(bars) < DROP_LOOKBACK + 1:
        return None
    prior = bars[-DROP_LOOKBACK - 1:-1]
    low_idx = min(range(len(prior)), key=lambda k: prior[k].close)
    if low_idx < len(prior) - BOTTOM_WITHIN:
        return None
    peak = max(b.close for b in prior[: low_idx + 1])
    bottom = prior[low_idx].close
    drop = (peak - bottom) / peak if peak else 0
    if drop < MIN_DROP:
        return None

    last = bars[-1]
    if last.close <= last.open:
        return None
    avg_vol = sum(b.volume for b in bars[-6:-1]) / 5.0
    if avg_vol > 0 and last.volume < avg_vol * VOL_RATIO:
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
        evidence={
            "drop_pct": round(drop * 100, 2),
            "days_since_bottom": len(prior) - 1 - low_idx,
            "ma5": ma5,
            "ma10": ma10,
        },
    )
