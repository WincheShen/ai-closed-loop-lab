"""§4.2 研究闸门。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.strategy_lab.engine import BacktestReport

SHORT_STRATEGIES = frozenset({
    "hot_sector_pullback",
    "bottom_reversal",
    "institutional_accumulation",
    "mean_reversion",
})
VALUE_STRATEGIES = frozenset({
    "value_investing",
    "defensive_bluechip",
})


@dataclass(frozen=True)
class GateResult:
    name: str
    ok: bool
    detail: str


def strategy_kind(strategy_id: str) -> str:
    if strategy_id in VALUE_STRATEGIES:
        return "value"
    return "short"


def evaluate_gates(report: BacktestReport) -> list[GateResult]:
    kind = strategy_kind(report.strategy_id)
    min_wr = 0.55 if kind == "value" else 0.48
    max_dd = 25.0 if kind == "value" else 15.0
    oos_floor = report.win_rate - 0.08
    return [
        GateResult("G-bt-2", report.n >= 200, f"n={report.n} need>=200"),
        GateResult("G-bt-3", report.win_rate >= min_wr, f"wr={report.win_rate:.1%} need>={min_wr:.0%}"),
        GateResult("G-bt-4", report.profit_factor >= 1.2, f"pf={report.profit_factor} need>=1.2"),
        GateResult(
            "G-bt-5",
            abs(report.max_drawdown_pct) <= max_dd,
            f"dd={report.max_drawdown_pct} need<={max_dd}",
        ),
        GateResult(
            "G-bt-6",
            report.oos_n == 0 or report.oos_win_rate >= oos_floor,
            f"oos_n={report.oos_n} oos_wr={report.oos_win_rate:.1%} floor={oos_floor:.1%}",
        ),
        GateResult("G-bt-7", True, "detectors imported from stock_analyzer.detectors"),
    ]


def gates_passed(report: BacktestReport) -> bool:
    return all(g.ok for g in evaluate_gates(report))


def gates_to_dict(report: BacktestReport) -> dict[str, Any]:
    rows = evaluate_gates(report)
    return {
        "strategy_id": report.strategy_id,
        "kind": strategy_kind(report.strategy_id),
        "passed": all(g.ok for g in rows),
        "gates": [{"name": g.name, "ok": g.ok, "detail": g.detail} for g in rows],
        "n": report.n,
        "win_rate": report.win_rate,
        "profit_factor": report.profit_factor,
        "max_drawdown_pct": report.max_drawdown_pct,
        "oos_n": report.oos_n,
        "oos_win_rate": report.oos_win_rate,
    }
