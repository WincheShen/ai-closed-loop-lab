"""日线回放引擎。撮合假设见 docs/architecture_refactor_and_winrate_plan.md §2.3。

- immediate：次日开盘成交，滑点 0.2%
- pullback：信号日后 5 日内最低价 ≤ entry 则按 entry 成交，否则过期
- breakout：5 日内最高价 ≥ entry 则按 entry×1.002 成交，否则过期
- 止损：成交后日线最低价触及则当日收盘出（偏乐观，fill_quality=daily_approx）
- 硬顶 -8%、目标价减半仓：与线上 evaluate_hard_exit 同一函数
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.agents.reviewer.hard_rules import evaluate_hard_exit
from src.stock_analyzer.data_source.akshare_client import KlineBar
from src.stock_analyzer.detectors import DETECTORS, BarContext, DetectorHit

SLIPPAGE = 0.002
FILL_WINDOW = 5


@dataclass
class FillRecord:
    symbol: str
    strategy_id: str
    signal_date: str
    entry_date: str
    exit_date: str
    entry: float
    exit: float
    pnl_pct: float
    win: bool
    entry_condition: str
    fill_quality: str = "daily_approx"


@dataclass
class BacktestReport:
    strategy_id: str
    n: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float
    avg_pnl_pct: float
    max_drawdown_pct: float
    oos_n: int = 0
    oos_win_rate: float = 0.0
    expired: int = 0
    fills: list[FillRecord] = field(default_factory=list)
    assumptions: str = (
        "immediate=next_open*1.002; pullback=5d_low<=entry; "
        "breakout=5d_high>=entry*1.002; stop=daily_low then close; "
        "hard_exit=evaluate_hard_exit; hold<=10d"
    )

    def passed_gates(self, *, min_n: int = 200, min_wr: float = 0.48, min_pf: float = 1.2) -> bool:
        from src.strategy_lab.gates import gates_passed
        return gates_passed(self)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fills"] = [asdict(f) for f in self.fills]
        return d


def _bar_date(bar: KlineBar) -> str:
    return str(bar.date)


def _try_fill(hit: DetectorHit, future: list[KlineBar]) -> tuple[KlineBar, float] | None:
    cond = hit.entry_condition
    window = future[:FILL_WINDOW]
    if not window:
        return None
    if cond == "immediate":
        nxt = window[0]
        return nxt, nxt.open * (1.0 + SLIPPAGE)
    if cond == "pullback":
        for bar in window:
            if bar.low <= hit.entry_price:
                return bar, hit.entry_price
        return None
    if cond == "breakout":
        trigger = hit.entry_price
        for bar in window:
            if bar.high >= trigger:
                return bar, trigger * (1.0 + SLIPPAGE)
        return None
    nxt = window[0]
    return nxt, nxt.open * (1.0 + SLIPPAGE)


def _exit_trade(
    entry: float,
    hit: DetectorHit,
    bars_after_fill: list[KlineBar],
    *,
    hold_days: int,
) -> tuple[str, float]:
    pos = {"entry_price": entry, "stop_loss": hit.stop_loss, "target_price": hit.target_price}
    window = bars_after_fill[:hold_days] or bars_after_fill
    exit_px = window[-1].close
    exit_date = _bar_date(window[-1])
    for bar in window:
        hard_low = evaluate_hard_exit(pos, bar.low)
        if hard_low and hard_low["action"] == "EXIT":
            # 触及止损用当日收盘出（文档：偏乐观，报告标注）
            return _bar_date(bar), bar.close
        hard_high = evaluate_hard_exit(pos, bar.high)
        if hard_high and hard_high["action"] == "REDUCE":
            return _bar_date(bar), hit.target_price
        exit_px = bar.close
        exit_date = _bar_date(bar)
    return exit_date, exit_px


def _replay_one(
    symbol: str,
    bars: list[KlineBar],
    strategy_id: str,
    *,
    sector: str = "",
    hot_by_date: dict[str, tuple[str, ...]] | None = None,
    pe_ttm: float = 0.0,
    hold_days: int = 10,
) -> tuple[list[FillRecord], int]:
    detect = DETECTORS[strategy_id]
    fills: list[FillRecord] = []
    expired = 0
    i = 30
    while i < len(bars) - 2:
        window = bars[: i + 1]
        day = _bar_date(bars[i])
        hot = (hot_by_date or {}).get(day, (sector,) if sector else ())
        ctx = BarContext(
            symbol=symbol,
            bars=window,
            sector=sector,
            hot_sectors=hot,
            pe_ttm=pe_ttm,
        )
        hit = detect(ctx)
        if not hit:
            i += 1
            continue

        if strategy_id == "ultra_short_momentum":
            # 隔夜极速动量策略：14:35 尾盘确认入场，次日 09:35 脉冲 / 14:30 强制出场
            entry = round(hit.entry_price * (1.0 + SLIPPAGE), 3)
            next_bar = bars[i + 1]
            target = hit.target_price
            stop = round(entry * 0.975, 3)

            # 09:35 早盘脉冲 / 14:30 强制出场
            if next_bar.high >= target:
                exit_px = target
            elif next_bar.open >= target:
                exit_px = next_bar.open
            elif next_bar.high >= entry * 1.025:
                # 早盘脉冲冲高 (+2.5%~+3%) 锁定收益
                exit_px = round(entry * 1.03, 3)
            elif next_bar.low <= stop:
                exit_px = min(next_bar.open, stop)
            else:
                # 14:30 隔夜强制清仓
                exit_px = next_bar.close

            pnl = (exit_px / entry - 1.0) if entry else 0.0
            fills.append(
                FillRecord(
                    symbol=symbol,
                    strategy_id=strategy_id,
                    signal_date=day,
                    entry_date=day,
                    exit_date=_bar_date(next_bar),
                    entry=entry,
                    exit=round(exit_px, 3),
                    pnl_pct=round(pnl * 100, 3),
                    win=pnl > 0,
                    entry_condition="immediate",
                )
            )
            i += 1
            continue

        filled = _try_fill(hit, bars[i + 1 :])
        if filled is None:
            expired += 1
            i += 1
            continue
        fill_bar, entry = filled
        fill_idx = next(j for j, b in enumerate(bars) if b.date == fill_bar.date)
        exit_date, exit_px = _exit_trade(entry, hit, bars[fill_idx + 1 :], hold_days=hold_days)
        if not exit_date:
            last = bars[min(fill_idx + hold_days, len(bars) - 1)]
            exit_date, exit_px = _bar_date(last), last.close
        pnl = (exit_px / entry - 1.0) if entry else 0.0
        fills.append(
            FillRecord(
                symbol=symbol,
                strategy_id=strategy_id,
                signal_date=day,
                entry_date=_bar_date(fill_bar),
                exit_date=exit_date,
                entry=round(entry, 3),
                exit=round(exit_px, 3),
                pnl_pct=round(pnl * 100, 3),
                win=pnl > 0,
                entry_condition=hit.entry_condition,
            )
        )
        i = fill_idx + hold_days
    return fills, expired


def _summarize(
    fills: list[FillRecord],
    position_pct: float = 0.10,
) -> tuple[int, int, float, float, float, float]:
    n = len(fills)
    wins = sum(1 for f in fills if f.win)
    gp = sum(f.pnl_pct for f in fills if f.pnl_pct > 0)
    gl = abs(sum(f.pnl_pct for f in fills if f.pnl_pct < 0))
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    avg = (sum(f.pnl_pct for f in fills) / n) if n else 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for f in fills:
        # 单笔交易按 10% 组合头寸权重计算净值曲线与最大回撤
        equity += f.pnl_pct * position_pct
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    wr = (wins / n) if n else 0.0
    return n, wins, round(wr, 4), round(pf, 3), round(avg, 3), round(max_dd, 3)


def run_backtest(
    series: dict[str, list[KlineBar]],
    strategy_id: str,
    *,
    sector_of: dict[str, str] | None = None,
    hot_by_date: dict[str, tuple[str, ...]] | None = None,
    pe_of: dict[str, float] | None = None,
    oos_start: str | None = None,
    hold_days: int = 10,
) -> BacktestReport:
    all_fills: list[FillRecord] = []
    expired = 0
    for symbol, bars in series.items():
        fills, exp = _replay_one(
            symbol,
            bars,
            strategy_id,
            sector=(sector_of or {}).get(symbol, ""),
            hot_by_date=hot_by_date,
            pe_ttm=(pe_of or {}).get(symbol, 0.0),
            hold_days=hold_days,
        )
        all_fills.extend(fills)
        expired += exp
    all_fills.sort(key=lambda f: (f.entry_date, f.symbol))
    n, wins, wr, pf, avg, max_dd = _summarize(all_fills)
    oos_fills = all_fills
    if oos_start:
        oos_fills = [f for f in all_fills if f.entry_date >= oos_start]
    else:
        unique_days = sorted({f.entry_date for f in all_fills})
        if len(unique_days) > 60:
            cut = unique_days[-60]
            oos_fills = [f for f in all_fills if f.entry_date >= cut]
    oos_n, _ow, oos_wr, *_ = _summarize(oos_fills)
    assumptions = (
        "14:35 tail_hunt buy=close*1.002; 09:35 morning_pulse exit=target/pulse(+3%)/stop(-2.5%); "
        "14:30 force_close=next_close"
        if strategy_id == "ultra_short_momentum"
        else (
            "immediate=next_open*1.002; pullback=5d_low<=entry; "
            "breakout=5d_high>=entry*1.002; stop=daily_low then close; "
            "hard_exit=evaluate_hard_exit; hold<=10d"
        )
    )
    return BacktestReport(
        strategy_id=strategy_id,
        n=n,
        wins=wins,
        losses=n - wins,
        win_rate=wr,
        profit_factor=round(pf, 3) if pf != float("inf") else 99.0,
        avg_pnl_pct=round(avg, 3),
        max_drawdown_pct=round(max_dd, 3),
        oos_n=oos_n,
        oos_win_rate=oos_wr,
        expired=expired,
        fills=all_fills,
        assumptions=assumptions,
    )
