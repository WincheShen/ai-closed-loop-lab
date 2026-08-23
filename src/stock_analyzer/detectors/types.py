"""检测器公共类型。线上 SignalFactory 与离线 strategy_lab 必须 import 同一份。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from src.stock_analyzer.data_source.akshare_client import KlineBar

EntryCondition = Literal["immediate", "pullback", "breakout"]


@dataclass(frozen=True)
class BarContext:
    symbol: str
    bars: list[KlineBar]
    sector: str = ""
    hot_sectors: tuple[str, ...] = ()
    inst_net_buy_wan: float = 0.0
    pe_ttm: float = 0.0
    turnover_rate: float = 0.0


@dataclass(frozen=True)
class DetectorHit:
    strategy_id: str
    entry_price: float
    stop_loss: float
    target_price: float
    entry_condition: EntryCondition
    position_pct_cap: float
    evidence: dict = field(default_factory=dict)


def last_close(bars: list[KlineBar]) -> float:
    return float(bars[-1].close) if bars else 0.0


def sma(bars: list[KlineBar], n: int) -> float | None:
    if len(bars) < n:
        return None
    closes = [b.close for b in bars[-n:]]
    return sum(closes) / n


def recent_low(bars: list[KlineBar], n: int) -> float:
    window = bars[-n:] if len(bars) >= n else bars
    return min(b.low for b in window) if window else 0.0


def recent_high(bars: list[KlineBar], n: int) -> float:
    window = bars[-n:] if len(bars) >= n else bars
    return max(b.high for b in window) if window else 0.0


def capped_stop(entry: float, raw_stop: float, max_loss_pct: float = 0.08) -> float:
    floor = entry * (1.0 - max_loss_pct)
    return max(raw_stop, floor) if raw_stop > 0 else floor
