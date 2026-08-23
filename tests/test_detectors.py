"""检测器 fixture：命中 / 不命中。"""
from __future__ import annotations

from datetime import date, timedelta

from src.stock_analyzer.data_source.akshare_client import KlineBar
from src.stock_analyzer.detectors.bottom_reversal import detect as detect_bottom
from src.stock_analyzer.detectors.hot_sector_pullback import detect as detect_pullback
from src.stock_analyzer.detectors.types import BarContext
from src.stock_analyzer.detectors.value_investing import detect as detect_value
from src.strategy_lab.engine import run_backtest


def _bars(closes: list[float], start: date = date(2024, 1, 2)) -> list[KlineBar]:
    out = []
    d = start
    for i, c in enumerate(closes):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        prev = closes[i - 1] if i else c
        high = max(c, prev) * 1.01
        low = min(c, prev) * 0.99
        out.append(
            KlineBar(
                date=d, open=prev, high=high, low=low, close=c,
                volume=10000 + i * 100, turnover=1e8 + i,
                change_pct=(c / prev - 1) * 100 if prev else 0,
            )
        )
        d += timedelta(days=1)
    return out


def test_pullback_hit_and_miss() -> None:
    # 先抬高再回撤约 8%
    closes = [10 + i * 0.2 for i in range(16)] + [12.2, 12.0, 11.8, 11.6]
    bars = _bars(closes)
    ctx = BarContext("000001", bars, sector="半导体", hot_sectors=("半导体",))
    hit = detect_pullback(ctx)
    assert hit is not None
    assert hit.strategy_id == "hot_sector_pullback"
    miss = detect_pullback(BarContext("000001", bars, sector="白酒", hot_sectors=("半导体",)))
    assert miss is None


def test_bottom_reversal_requires_cross() -> None:
    # 连跌后一根放量阳线但均线未金叉 → 不命中
    closes = [20 - i * 0.4 for i in range(10)] + [16.2]
    bars = _bars(closes)
    # 放大最后一根量
    last = bars[-1]
    bars[-1] = KlineBar(
        date=last.date, open=15.5, high=16.4, low=15.4, close=16.2,
        volume=1e6, turnover=2e8,
    )
    assert detect_bottom(BarContext("000002", bars)) is None


def test_value_skips_new_high() -> None:
    closes = [10 + i * 0.3 for i in range(20)]
    bars = _bars(closes)
    assert detect_value(BarContext("600519", bars, pe_ttm=18)) is None


def test_backtest_deterministic() -> None:
    closes = [10 + i * 0.2 for i in range(16)] + [12.2, 12.0, 11.8, 11.6] + [11.5] * 15
    bars = _bars(closes)
    r1 = run_backtest(
        {"000001": bars},
        "hot_sector_pullback",
        sector_of={"000001": "半导体"},
        hot_by_date={str(b.date): ("半导体",) for b in bars},
    )
    r2 = run_backtest(
        {"000001": bars},
        "hot_sector_pullback",
        sector_of={"000001": "半导体"},
        hot_by_date={str(b.date): ("半导体",) for b in bars},
    )
    assert r1.n == r2.n
    assert [f.pnl_pct for f in r1.fills] == [f.pnl_pct for f in r2.fills]
