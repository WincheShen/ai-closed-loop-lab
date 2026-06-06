#!/usr/bin/env python3
"""补跑已平仓但缺少归因的 position。

用法:
    python scripts/backfill_attributions.py          # 补跑所有缺失
    python scripts/backfill_attributions.py --dry-run # 仅诊断，不写入
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agents.memory.trade_attribution import TradeAttributor
from src.central_brain import get_central_brain


def find_unattributed_positions() -> list[dict]:
    """查找所有已平仓但无归因的 position。"""
    brain = get_central_brain()
    conn = brain.store._conn()
    rows = conn.execute(
        """SELECT p.* FROM positions p
        LEFT JOIN trade_attributions ta ON p.position_id = ta.position_id
        WHERE p.status = 'closed' AND ta.attribution_id IS NULL
        ORDER BY p.closed_at DESC""",
    ).fetchall()
    return [dict(r) for r in rows]


def backfill(dry_run: bool = False) -> None:
    positions = find_unattributed_positions()
    if not positions:
        print("All closed positions already have attributions.")
        return

    print(f"Found {len(positions)} closed position(s) without attribution:\n")
    for p in positions:
        pnl_pct = 0.0
        if p["entry_price"] and p["close_price"] and p["entry_price"] > 0:
            pnl_pct = (p["close_price"] - p["entry_price"]) / p["entry_price"] * 100
        print(
            f"  {p['position_id']} | {p['symbol']} {p.get('name', '')} | "
            f"entry={p['entry_price']:.2f} close={p.get('close_price', 0):.2f} | "
            f"pnl={p.get('realized_pnl', 0):.2f} ({pnl_pct:+.2f}%) | "
            f"closed_at={p.get('closed_at', 'N/A')}"
        )

    if dry_run:
        print("\n[DRY RUN] No changes written.")
        return

    print("\nRunning attribution engine...\n")
    attributor = TradeAttributor("backfill")

    for p in positions:
        try:
            attr = attributor.attribute_and_save(p, close_price=p.get("close_price"))
            print(
                f"  OK  {p['symbol']} {p.get('name', '')} | "
                f"{attr.attribution_id} | {attr.outcome} | "
                f"cause={attr.primary_cause} | lesson={attr.lesson[:50] if attr.lesson else 'N/A'}"
            )
        except Exception as e:
            print(f"  ERR {p['symbol']}: {e}")

    print("\nBackfill complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill trade attributions")
    parser.add_argument("--dry-run", action="store_true", help="Diagnose only")
    args = parser.parse_args()
    backfill(dry_run=args.dry_run)
