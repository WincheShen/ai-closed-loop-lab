"""Infer trade events from daily holding snapshots.

Within one `account_session_id`, we walk chronologically and compare
symbol-level (qty, cost) day-over-day:

-  symbol appears for first time        -> buy_open
-  symbol re-appears after gap/absence   -> re_enter
-  qty increases                         -> add (back-calc entry price)
-  qty decreases to 0                  -> sell_close
-  qty decreases but >0                  -> reduce
-  symbol disappears from list           -> sell_close (inferred)
-  account session changes               -> broker_transition (not a trade)
-  gap >1 trading day between snapshots -> is_inter_period=true

Cost basis is assumed to be move-weighted average (Chinese broker convention):
- On add: new_avg = (old_qty*old_avg + bought_qty*buy_price) / new_qty
  We can back-calculate buy_price when both old and new are known.
- On sell/reduce: avg cost of remaining shares stays unchanged.
  Therefore realized PnL = (est_sale_price - old_avg) * sold_qty.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import akshare as ak
import pandas as pd

from ..db import connection_scope

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Simple trading-day calendar (SH/SZ markets are roughly Mon-Fri minus holidays).
# For this short-range analysis we just count calendar days; gaps >=3 days
# across weekends are flagged conservatively.
# ---------------------------------------------------------------------------
def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def _trading_day_gap(a: date, b: date) -> int:
    """Rough trading-day gap between two dates (positive if b > a)."""
    if b <= a:
        return 0
    delta = (b - a).days
    # weekends subtract 2 per full week + any remaining
    wk = 0
    dtmp = a
    while dtmp < b:
        if _is_weekend(dtmp):
            wk += 1
        dtmp += timedelta(days=1)
    return max(0, delta - wk)


# ---------------------------------------------------------------------------
# Close-price helper (cached by process)
# ---------------------------------------------------------------------------
_close_cache: dict[tuple[str, date], float | None] = {}


def _get_close(symbol: str, d: date) -> float | None:
    key = (symbol, d)
    if key in _close_cache:
        return _close_cache[key]
    ds = d.strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=ds, end_date=ds, adjust="")
    except Exception as exc:  # pragma: no cover
        logger.warning("akshare close failed %s@%s: %s", symbol, ds, exc)
        _close_cache[key] = None
        return None
    if df is None or df.empty:
        _close_cache[key] = None
        return None
    for col in ("收盘", "close", "收盘价"):
        if col in df.columns:
            val = float(df.iloc[-1][col])
            _close_cache[key] = val
            return val
    _close_cache[key] = None
    return None


# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InferredTrade:
    trade_date: date
    prev_trade_date: date | None
    gap_days: int
    is_inter_period: bool
    account_session_id: str | None
    account_session_id_prev: str | None
    symbol: str
    stock_name_full: str | None
    event_type: str  # buy_open / add / reduce / sell_close / re_enter / broker_transition / unknown
    prev_qty: int | None
    new_qty: int | None
    delta_qty: int
    prev_cost: float | None
    new_cost: float | None
    trade_price_estimate: float | None
    price_estimate_source: str
    proceeds_estimate: float | None  # delta_qty * price (negative for buys)
    realized_pnl: float | None
    holding_days_at_close: int | None
    confidence: float
    notes: str | None = None
    derivation_method: str = "snapshot_diff"


# ---------------------------------------------------------------------------
# Core diff logic
# ---------------------------------------------------------------------------
def _back_calc_buy_price(
    old_qty: int, old_cost: float,
    new_qty: int, new_cost: float,
) -> float | None:
    """Back-calculate the price of newly added shares."""
    bought = new_qty - old_qty
    if bought <= 0:
        return None
    total_new_cost = new_qty * new_cost
    total_old_cost = old_qty * old_cost
    buy_price = (total_new_cost - total_old_cost) / bought
    return round(buy_price, 4)


def _diff_pair(
    prev_date: date,
    curr_date: date,
    prev_items: list[dict[str, Any]],
    curr_items: list[dict[str, Any]],
    prev_session: str | None,
    curr_session: str | None,
) -> list[InferredTrade]:
    """Compare holdings between two consecutive snapshot dates."""
    gap = _trading_day_gap(prev_date, curr_date)
    inter = gap > 1

    trades: list[InferredTrade] = []

    # Broker transition
    if prev_session != curr_session and prev_session and curr_session:
        # We still diff the stocks, but every event is marked broker_transition
        # Actually better: just emit a single broker_transition event per symbol
        # and stop trying to infer trades across sessions.
        prev_syms = {it["symbol"]: it for it in prev_items if it["symbol"]}
        curr_syms = {it["symbol"]: it for it in curr_items if it["symbol"]}
        for sym in set(prev_syms) | set(curr_syms):
            p = prev_syms.get(sym)
            c = curr_syms.get(sym)
            trades.append(InferredTrade(
                trade_date=curr_date,
                prev_trade_date=prev_date,
                gap_days=gap,
                is_inter_period=inter,
                account_session_id=curr_session,
                account_session_id_prev=prev_session,
                symbol=sym,
                stock_name_full=(c or p).get("stock_name_full"),
                event_type="broker_transition",
                prev_qty=p["position_qty"] if p else 0,
                new_qty=c["position_qty"] if c else 0,
                delta_qty=(c["position_qty"] if c else 0) - (p["position_qty"] if p else 0),
                prev_cost=p["cost_price"] if p else None,
                new_cost=c["cost_price"] if c else None,
                trade_price_estimate=None,
                price_estimate_source="n/a",
                proceeds_estimate=None,
                realized_pnl=None,
                holding_days_at_close=None,
                confidence=0.5,
                notes=f"broker switch {prev_session} -> {curr_session}",
            ))
        return trades

    prev_syms = {it["symbol"]: it for it in prev_items if it["symbol"]}
    curr_syms = {it["symbol"]: it for it in curr_items if it["symbol"]}
    all_syms = set(prev_syms) | set(curr_syms)

    for sym in all_syms:
        p = prev_syms.get(sym)
        c = curr_syms.get(sym)
        pq = p["position_qty"] if p else 0
        cq = c["position_qty"] if c else 0
        pcost = float(p["cost_price"]) if p and p["cost_price"] is not None else None
        ccost = float(c["cost_price"]) if c and c["cost_price"] is not None else None
        delta = cq - pq

        # Defaults
        event = "unknown"
        price_est: float | None = None
        price_src = "none"
        proceeds: float | None = None
        realized: float | None = None
        hold_days: int | None = None
        conf = 0.8
        notes: str | None = None

        if p is None and c is not None:
            # Appeared
            if inter:
                event = "buy_open"  # could be re-enter but we don't know
                conf = 0.5
                notes = "first appearance after gap"
            else:
                event = "buy_open"
            price_est = ccost  # best guess: cost shown is close to buy price
            price_src = "snapshot_cost"
            if cq > 0:
                proceeds = -round(cq * (price_est or 0), 2)

        elif p is not None and c is None:
            # Disappeared — use last known current_price from prev snapshot
            event = "sell_close"
            price_est = float(p["current_price"]) if p.get("current_price") is not None else pcost
            price_src = "snapshot_current_price"
            if pq > 0 and pcost is not None and price_est is not None:
                realized = round((price_est - pcost) * pq, 2)
                proceeds = round(price_est * pq, 2)
            hold_days = 1  # at least held through prev day
            if p.get("first_seen_date"):
                hold_days = max(1, (curr_date - p["first_seen_date"]).days)

        elif delta > 0:
            # Added
            event = "add"
            back_price = _back_calc_buy_price(pq, pcost or 0, cq, ccost or 0)
            if back_price and back_price > 0:
                price_est = back_price
                price_src = "back_calc_blend"
            else:
                price_est = ccost
                price_src = "snapshot_cost"
            proceeds = -round(delta * (price_est or 0), 2)
            conf = 0.75 if price_src == "back_calc_blend" else 0.6

        elif delta < 0:
            # Reduced or closed
            sold = -delta
            if cq == 0:
                event = "sell_close"
            else:
                event = "reduce"
            # Use current snapshot price (most accurate for that day)
            price_est = float(c["current_price"]) if c and c.get("current_price") is not None else (
                float(p["current_price"]) if p and p.get("current_price") is not None else pcost
            )
            price_src = "snapshot_current_price"
            if pcost is not None and price_est is not None:
                realized = round((price_est - pcost) * sold, 2)
            proceeds = round((price_est or 0) * sold, 2)
            hold_days = 1
            if p.get("first_seen_date"):
                hold_days = max(1, (curr_date - p["first_seen_date"]).days)

        else:
            # delta == 0 : no qty change
            if c and c.get("is_zero_position") and not (p and p.get("is_zero_position")):
                # Explicit 0/0 row appeared today (already marked zero yesterday means sold)
                event = "sell_close"
                sold = pq
                price_est = float(c["current_price"]) if c.get("current_price") is not None else (
                    float(p["current_price"]) if p and p.get("current_price") is not None else pcost
                )
                price_src = "snapshot_current_price"
                if pcost is not None and price_est is not None:
                    realized = round((price_est - pcost) * sold, 2)
                proceeds = round((price_est or 0) * sold, 2)
                hold_days = max(1, (curr_date - (p.get("first_seen_date") or prev_date)).days)
            else:
                continue  # nothing to record

        if inter and event not in ("broker_transition",):
            conf *= 0.7
            notes = (notes or "") + "; inter-period gap" if notes else "inter-period gap"

        trades.append(InferredTrade(
            trade_date=curr_date,
            prev_trade_date=prev_date,
            gap_days=gap,
            is_inter_period=inter,
            account_session_id=curr_session,
            account_session_id_prev=prev_session,
            symbol=sym,
            stock_name_full=(c or p).get("stock_name_full"),
            event_type=event,
            prev_qty=pq if p else None,
            new_qty=cq if c else None,
            delta_qty=delta,
            prev_cost=pcost,
            new_cost=ccost,
            trade_price_estimate=price_est,
            price_estimate_source=price_src,
            proceeds_estimate=proceeds,
            realized_pnl=realized,
            holding_days_at_close=hold_days,
            confidence=round(conf, 3),
            notes=notes,
        ))

    return trades


# ---------------------------------------------------------------------------
def infer_trades(trader_alias: str) -> list[InferredTrade]:
    """Read all snapshots for a trader, infer trades, return list."""
    sql = """
        SELECT s.id, s.trade_date, s.account_session_id,
               i.symbol, i.stock_name_full, i.position_qty, i.available_qty,
               i.cost_price, i.current_price, i.is_zero_position
          FROM strategy_mining.holding_snapshots s
          JOIN strategy_mining.holding_items i ON i.snapshot_id = s.id
         WHERE s.trader_alias = %s
           AND s.page_type = 'position_page'
         ORDER BY s.trade_date, i.row_index;
    """
    with connection_scope(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, (trader_alias,))
        rows = cur.fetchall()

    # Group by snapshot
    from collections import defaultdict
    snapshots: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "date": None, "session": None, "items": []
    })
    first_seen: dict[str, date] = {}
    for snap_id, td, sess, sym, name, qty, avail, cost, curpx, is_zero in rows:
        snapshots[snap_id]["date"] = td
        snapshots[snap_id]["session"] = sess
        item = {
            "symbol": sym,
            "stock_name_full": name,
            "position_qty": qty or 0,
            "available_qty": avail or 0,
            "cost_price": cost,
            "current_price": curpx,
            "is_zero_position": is_zero,
        }
        snapshots[snap_id]["items"].append(item)
        if sym and sym not in first_seen:
            first_seen[sym] = td

    # Annotate first_seen_date on each item for holding-days calc
    for snap in snapshots.values():
        for it in snap["items"]:
            it["first_seen_date"] = first_seen.get(it["symbol"])

    # Sort chronologically
    ordered = sorted(snapshots.values(), key=lambda x: x["date"])

    trades: list[InferredTrade] = []
    for i in range(1, len(ordered)):
        prev = ordered[i - 1]
        curr = ordered[i]
        trades.extend(_diff_pair(
            prev["date"], curr["date"],
            prev["items"], curr["items"],
            prev["session"], curr["session"],
        ))

    return trades
