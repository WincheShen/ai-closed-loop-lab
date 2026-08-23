"""最小日线回放引擎。撮合假设见 docs/architecture_refactor_and_winrate_plan.md §2.3。"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.agents.reviewer.hard_rules import evaluate_hard_exit
from src.stock_analyzer.data_source.akshare_client import KlineBar
from src.stock_analyzer.detectors import DETECTORS, BarContext


@dataclass
class FillRecord:
    symbol: str
    strategy_id: str
    entry_date: str
    exit_date: str
    entry: float
    exit: float
    pnl_pct: float
    win: bool
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
    fills: list[FillRecord] = field(default_factory=list)

    def passed_gates(self, *, min_n: int = 200, min_wr: float = 0.48, min_pf: float = 1.2) -> bool:
        return self.n >= min_n and self.win_rate >= min_wr and self.profit_factor >= min_pf


def _replay_one(
    symbol: str,
    bars: list[KlineBar],
    strategy_id: str,
    *,
    sector: str = "",
    hot_by_date: dict[str, tuple[str, ...]] | None = None,
    hold_days: int = 10,
) -> list[FillRecord]:
    detect = DETECTORS[strategy_id]
    fills: list[FillRecord] = []
    i = 30
    while i < len(bars) - 2:
        window = bars[: i + 1]
        day = str(bars[i].date)
        hot = (hot_by_date or {}).get(day, (sector,) if sector else ())
        ctx = BarContext(symbol=symbol, bars=window, sector=sector, hot_sectors=hot)
        hit = detect(ctx)
        if not hit:
            i += 1
            continue
        # 次日开盘成交 + 0.2% 滑点
        nxt = bars[i + 1]
        entry = nxt.open * 1.002
        exit_px = nxt.close
        exit_date = str(nxt.date)
        pos = {"entry_price": entry, "stop_loss": hit.stop_loss, "target_price": hit.target_price}
        closed = False
        for j in range(i + 1, min(i + 1 + hold_days, len(bars))):
            bar = bars[j]
            # 用最低价近似止损触及（偏乐观，报告标注 daily_approx）
            hard = evaluate_hard_exit(pos, bar.low)
            if hard and hard["action"] == "EXIT":
                exit_px = min(bar.open, hit.stop_loss)
                exit_date = str(bar.date)
                closed = True
                break
            hard_t = evaluate_hard_exit(pos, bar.high)
            if hard_t and hard_t["action"] == "REDUCE":
                exit_px = hit.target_price
                exit_date = str(bar.date)
                closed = True
                break
            exit_px = bar.close
            exit_date = str(bar.date)
        if not closed:
            last = bars[min(i + hold_days, len(bars) - 1)]
            exit_px = last.close
            exit_date = str(last.date)
        pnl = (exit_px / entry - 1.0) if entry else 0.0
        fills.append(
            FillRecord(
                symbol=symbol,
                strategy_id=strategy_id,
                entry_date=str(nxt.date),
                exit_date=exit_date,
                entry=round(entry, 3),
                exit=round(exit_px, 3),
                pnl_pct=round(pnl * 100, 3),
                win=pnl > 0,
            )
        )
        i += hold_days  # 避免重叠持仓
    return fills


def run_backtest(
    series: dict[str, list[KlineBar]],
    strategy_id: str,
    *,
    sector_of: dict[str, str] | None = None,
    hot_by_date: dict[str, tuple[str, ...]] | None = None,
) -> BacktestReport:
    all_fills: list[FillRecord] = []
    for symbol, bars in series.items():
        all_fills.extend(
            _replay_one(
                symbol, bars, strategy_id,
                sector=(sector_of or {}).get(symbol, ""),
                hot_by_date=hot_by_date,
            )
        )
    n = len(all_fills)
    wins = sum(1 for f in all_fills if f.win)
    losses = n - wins
    gp = sum(f.pnl_pct for f in all_fills if f.pnl_pct > 0)
    gl = abs(sum(f.pnl_pct for f in all_fills if f.pnl_pct < 0))
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for f in all_fills:
        equity += f.pnl_pct
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return BacktestReport(
        strategy_id=strategy_id,
        n=n,
        wins=wins,
        losses=losses,
        win_rate=(wins / n) if n else 0.0,
        profit_factor=round(pf, 3) if pf != float("inf") else 99.0,
        avg_pnl_pct=round(sum(f.pnl_pct for f in all_fills) / n, 3) if n else 0.0,
        max_drawdown_pct=round(max_dd, 3),
        fills=all_fills,
    )
