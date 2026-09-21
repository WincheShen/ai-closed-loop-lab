"""隔夜极速动量：高弹性板块 + 流动性/振幅闸门 + 强势延续或强势股回踩企稳。

设计来源：对一份激进短线实盘名单的日 K 归因 —— 标的集中于创业板/科创板/
北交所/深市题材股（无沪市主板），日成交额大、振幅高，赚次日情绪溢价，
市场宽度极差时强制空仓。日线无法还原盘中买卖点，本检测器只固化可验证的
收盘级特征：板块弹性、流动性、振幅、动量/回撤形态、市场宽度。
"""
from __future__ import annotations

from src.stock_analyzer.data_source.akshare_client import KlineBar
from src.stock_analyzer.detectors.types import (
    BarContext,
    DetectorHit,
    capped_stop,
    last_close,
    recent_high,
    recent_low,
)

STRATEGY_ID = "ultra_short_momentum"

# 允许的高弹性板块前缀：深市主板/中小、创业板(20CM)、科创板(20CM)、北交所(30CM)。
# 观察样本中无沪市主板标的；基金/债券/B股前缀天然不在此列。
_ALLOWED_PREFIXES = (
    "000", "001", "002", "003",
    "300", "301", "302",
    "688", "689",
    "430", "83", "87", "88", "920",
)

_MIN_BARS = 21              # 需要 bars[-21] 计算 20 日涨幅
_MIN_TURNOVER = 3e8         # 当日成交额 ≥ 3 亿，保证次日能全身而退
_MIN_AMPLITUDE = 0.05       # 当日振幅 ≥ 5%，无弹性不做隔夜博弈
_MIN_UP_RATIO = 0.30        # 市场上涨家数占比下限；up_ratio<0 表示未知则跳过
_MAX_FALL_KNIFE = -0.07     # 回踩形态中当日跌幅超过 -7% 不接飞刀

# 形态 A：强势延续
_CONT_RET5 = 0.05           # 5 日涨幅 ≥ 5%
_CONT_RET20 = 0.05          # 20 日涨幅 ≥ 5%（中期趋势未破坏）
_CONT_CLOSE_POS = 0.60      # 收盘位于当日振幅上 40% 区域
_CONT_VOL_FLOOR = 0.8       # 当日成交额不得低于 20 日均量的 80%

# 形态 B：强势股回踩反抽
_PB_RET20 = 0.08            # 20 日仍保留 ≥8% 涨幅（确认是强势股回踩而非弱势股）
_PB_DD_MIN = 0.04           # 距 10 日高点回撤 4%~15%
_PB_DD_MAX = 0.15

_STOP_CAP = 0.035           # 隔夜单止损上限 3.5%
_TARGET_PCT = 1.08          # 目标 +8%（次日早盘冲高兑现）


def _allowed_symbol(symbol: str) -> bool:
    return bool(symbol) and symbol.startswith(_ALLOWED_PREFIXES)


def _stabilized(last: KlineBar, close_pos: float) -> bool:
    """回踩日是否出现企稳迹象：收阳 / 收盘位置不极端低 / 下影线承接。"""
    if last.close >= last.open:
        return True
    rng = last.high - last.low
    if rng <= 0:
        return True
    lower_shadow = min(last.open, last.close) - last.low
    return close_pos >= 0.35 or (lower_shadow / rng) >= 0.4


def detect(ctx: BarContext) -> DetectorHit | None:
    if not _allowed_symbol(ctx.symbol):
        return None
    name = (ctx.name or "").upper()
    if "ST" in name or "退" in name:
        return None
    if 0 <= ctx.up_ratio < _MIN_UP_RATIO:
        return None

    bars = ctx.bars
    if len(bars) < _MIN_BARS:
        return None
    last = bars[-1]
    prev_close = bars[-2].close
    close = last_close(bars)
    if prev_close <= 0 or close <= 0:
        return None

    if (last.turnover or 0) < _MIN_TURNOVER:
        return None
    amplitude = (last.high - last.low) / prev_close
    if amplitude < _MIN_AMPLITUDE:
        return None

    ret5 = close / bars[-6].close - 1.0 if bars[-6].close else 0.0
    ret20 = close / bars[-21].close - 1.0 if bars[-21].close else 0.0
    high10 = recent_high(bars, 10)
    drawdown = (high10 - close) / high10 if high10 > 0 else 0.0
    rng = last.high - last.low
    close_pos = (close - last.low) / rng if rng > 0 else 1.0
    change = close / prev_close - 1.0

    setup = ""
    cap = 0.0
    if (
        ret5 >= _CONT_RET5
        and ret20 >= _CONT_RET20
        and change >= 0.0
        and close_pos >= _CONT_CLOSE_POS
    ):
        avg_to = sum(b.turnover for b in bars[-20:]) / 20.0
        # 延续形态要求量能不萎缩；回踩形态允许缩量
        if avg_to > 0 and last.turnover < avg_to * _CONT_VOL_FLOOR:
            return None
        setup = "momentum_continuation"
        cap = 0.12
    elif (
        ret20 >= _PB_RET20
        and _PB_DD_MIN <= drawdown <= _PB_DD_MAX
        and change >= _MAX_FALL_KNIFE
        and _stabilized(last, close_pos)
    ):
        setup = "strong_pullback"
        cap = 0.08
    if not setup:
        return None

    entry = round(close, 2)
    stop = capped_stop(entry, recent_low(bars, 5), max_loss_pct=_STOP_CAP)
    return DetectorHit(
        strategy_id=STRATEGY_ID,
        entry_price=entry,
        stop_loss=round(stop, 2),
        target_price=round(entry * _TARGET_PCT, 2),
        entry_condition="immediate",
        position_pct_cap=cap,
        evidence={
            "setup": setup,
            "ret5_pct": round(ret5 * 100, 2),
            "ret20_pct": round(ret20 * 100, 2),
            "drawdown_pct": round(drawdown * 100, 2),
            "close_pos": round(close_pos, 2),
            "amplitude_pct": round(amplitude * 100, 2),
            "turnover_yi": round((last.turnover or 0) / 1e8, 2),
            "up_ratio": ctx.up_ratio,
        },
    )
