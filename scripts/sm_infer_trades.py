"""CLI: 从持仓快照推断交易事件并写入 holding_trades。

用法：
    python scripts/sm_infer_trades.py --trader 二池
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from strategy_mining.holdings.diff_engine import infer_trades  # noqa: E402
from strategy_mining.holdings.trades_repo import save_trades  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trader", required=True)
    p.add_argument("--dry-run", action="store_true",
                   help="只打印，不写库")
    p.add_argument("--json", action="store_true",
                   help="打印 JSON 而不是表格")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    trades = infer_trades(args.trader)
    if not trades:
        print("No trades inferred.")
        return 0

    if args.dry_run or args.json:
        out = [
            {
                "date": t.trade_date.isoformat(),
                "event": t.event_type,
                "symbol": t.symbol,
                "name": t.stock_name_full,
                "delta_qty": t.delta_qty,
                "price_est": float(t.trade_price_estimate) if t.trade_price_estimate else None,
                "realized_pnl": float(t.realized_pnl) if t.realized_pnl else None,
                "confidence": t.confidence,
                "inter_period": t.is_inter_period,
                "notes": t.notes,
            }
            for t in trades
        ]
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            for o in out:
                flag = "⚠️" if o["inter_period"] else ""
                print(
                    f"  {o['date']} {o['event']:12s} {o['symbol']:8s} "
                    f"Δ={o['delta_qty']:>6}  price_est={o['price_est']}  "
                    f"pnl={o['realized_pnl']}  conf={o['confidence']:.2f}  {flag}"
                )
        if args.dry_run:
            print(f"\n(dry-run: {len(trades)} trades not written)")
            return 0

    cnt = save_trades(args.trader, trades)
    print(f"Wrote {cnt} inferred trades to strategy_mining.holding_trades")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
