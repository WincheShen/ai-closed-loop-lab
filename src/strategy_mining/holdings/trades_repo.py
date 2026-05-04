"""Persistence for inferred holding_trades."""
from __future__ import annotations

import logging
from typing import Any

from .diff_engine import InferredTrade
from ..db import connection_scope

logger = logging.getLogger(__name__)

TRADE_INSERT = """
INSERT INTO strategy_mining.holding_trades
  (trader_alias, trade_date, prev_trade_date, gap_days, is_inter_period,
   account_session_id, account_session_id_prev, symbol, stock_name_full,
   event_type, prev_qty, new_qty, delta_qty, prev_cost, new_cost,
   trade_price_estimate, price_estimate_source, proceeds_estimate,
   realized_pnl, holding_days_at_close, confidence, notes, derivation_method)
VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s)
"""


def save_trades(trader_alias: str, trades: list[InferredTrade]) -> int:
    """Save trades after clearing previous inferred trades for this trader."""
    if not trades:
        return 0
    with connection_scope() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM strategy_mining.holding_trades WHERE trader_alias = %s",
            (trader_alias,),
        )
        for t in trades:
            cur.execute(
                TRADE_INSERT,
                (
                    trader_alias,
                    t.trade_date, t.prev_trade_date, t.gap_days, t.is_inter_period,
                    t.account_session_id, t.account_session_id_prev,
                    t.symbol, t.stock_name_full, t.event_type,
                    t.prev_qty, t.new_qty, t.delta_qty,
                    t.prev_cost, t.new_cost,
                    t.trade_price_estimate, t.price_estimate_source,
                    t.proceeds_estimate, t.realized_pnl,
                    t.holding_days_at_close, t.confidence, t.notes,
                    t.derivation_method or "snapshot_diff",
                ),
            )
    logger.info("saved %d inferred trades for %s", len(trades), trader_alias)
    return len(trades)


def list_trades(trader_alias: str) -> list[dict[str, Any]]:
    sql = """
        SELECT trade_date, event_type, symbol, stock_name_full,
               delta_qty, trade_price_estimate, realized_pnl,
               confidence, is_inter_period, notes
          FROM strategy_mining.holding_trades
         WHERE trader_alias = %s
         ORDER BY trade_date, event_type;
    """
    with connection_scope(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, (trader_alias,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
