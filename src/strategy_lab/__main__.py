"""python -m src.strategy_lab --strategy hot_sector_pullback --start 2024-01-01"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from src.strategy_lab.data import DEFAULT_UNIVERSE, fetch_or_load
from src.strategy_lab.engine import run_backtest
from src.strategy_lab.gates import gates_to_dict
from src.strategy_lab.synthetic import universe_for

_REPORT_DIR = Path(__file__).resolve().parents[2] / "reports" / "backtests"


def _load_series(strategy_id: str, symbols: list[str], refresh: bool, synthetic: bool):
    if synthetic:
        return universe_for(strategy_id)
    series = {}
    for sym in symbols:
        try:
            bars = fetch_or_load(sym, refresh=refresh)
        except Exception as exc:
            print(f"skip {sym}: {exc}", file=sys.stderr)
            continue
        if bars:
            series[sym] = bars
    return series


def _write_report(report, gate: dict) -> Path:
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {**report.to_dict(), "gates": gate}
    payload.pop("fills", None)
    path = _REPORT_DIR / f"{report.strategy_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md = _REPORT_DIR / f"{report.strategy_id}.md"
    lines = [
        f"# {report.strategy_id}",
        "",
        f"- assumptions: {report.assumptions}",
        f"- n={report.n} wr={report.win_rate:.1%} pf={report.profit_factor} dd={report.max_drawdown_pct}",
        f"- oos_n={report.oos_n} oos_wr={report.oos_win_rate:.1%} expired={report.expired}",
        f"- passed={gate['passed']}",
        "",
    ]
    for g in gate["gates"]:
        lines.append(f"- [{'OK' if g['ok'] else 'NO'}] {g['name']}: {g['detail']}")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="strategy_lab 回测")
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--synthetic", action="store_true", help="用确定性合成行情（不算研究过闸）")
    args = parser.parse_args()

    symbols = [s for s in args.symbols.split(",") if s] or DEFAULT_UNIVERSE.get(args.strategy, [])
    series = _load_series(args.strategy, symbols, args.refresh, args.synthetic)
    if not series:
        print(json.dumps({"ok": False, "reason": "无日线"}, ensure_ascii=False))
        return 2

    start = date.fromisoformat(args.start)
    series = {
        sym: [b for b in bars if b.date >= start]
        for sym, bars in series.items()
    }
    sector_of = {sym: "半导体" for sym in series} if args.strategy == "hot_sector_pullback" else None
    hot_by_date = None
    if args.strategy == "hot_sector_pullback":
        days = {str(b.date) for bars in series.values() for b in bars}
        hot_by_date = {d: ("半导体",) for d in days}
    pe_of = {sym: 18.0 for sym in series} if args.strategy == "value_investing" else None
    report = run_backtest(
        series, args.strategy, sector_of=sector_of, hot_by_date=hot_by_date, pe_of=pe_of,
    )
    gate = gates_to_dict(report)
    if args.synthetic:
        gate["passed"] = False
        gate["note"] = "synthetic universe is not research evidence"
    path = _write_report(report, gate)
    out = {
        "ok": True,
        "strategy": args.strategy,
        "start": args.start,
        "synthetic": args.synthetic,
        "n": report.n,
        "win_rate": report.win_rate,
        "profit_factor": report.profit_factor,
        "max_drawdown_pct": report.max_drawdown_pct,
        "passed": gate["passed"],
        "report": str(path),
        "gates": gate["gates"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if (gate["passed"] or args.synthetic) else 1


if __name__ == "__main__":
    sys.exit(main())
