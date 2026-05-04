"""Persistence for holding_snapshots + holding_items.

Write is idempotent on (trader_alias, trade_date, account_suffix) — same
image re-ingested only updates raw_vlm_json and items.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from psycopg2.extras import Json

from ..db import connection_scope

logger = logging.getLogger(__name__)


@dataclass
class ItemRow:
    row_index: int
    stock_name_visible: str | None
    stock_name_obfuscation: str | None
    stock_name_full: str | None
    symbol: str | None
    exchange: str | None
    match_confidence: float | None
    match_method: str | None
    market_value: float | None
    pnl_amount: float | None
    pnl_pct: float | None
    position_qty: int | None
    available_qty: int | None
    cost_price: float | None
    current_price: float | None
    is_zero_position: bool
    needs_review: bool
    notes: str | None = None


@dataclass
class SnapshotRow:
    trader_alias: str
    trade_date: date
    page_type: str
    broker_name: str | None
    account_suffix: str | None
    account_session_id: str | None
    total_assets: float | None
    total_pnl: float | None
    total_pnl_today: float | None
    total_pnl_today_pct: float | None
    market_value: float | None
    available_cash: float | None
    withdrawable_cash: float | None
    position_pct: float | None
    holding_count: int
    active_holding_count: int
    source_image_path: str
    source_image_sha256: str
    raw_vlm_json: dict[str, Any]
    vlm_model: str | None
    vlm_confidence: float | None
    needs_review: bool
    notes: str | None
    items: list[ItemRow]


SNAPSHOT_UPSERT = """
INSERT INTO strategy_mining.holding_snapshots
  (trader_alias, trade_date, page_type, broker_name, account_suffix,
   account_session_id, total_assets, total_pnl, total_pnl_today,
   total_pnl_today_pct, market_value, available_cash, withdrawable_cash,
   position_pct, holding_count, active_holding_count,
   source_image_path, source_image_sha256, raw_vlm_json,
   vlm_model, vlm_confidence, needs_review, notes)
VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s, %s,%s,%s,%s)
ON CONFLICT (trader_alias, trade_date, account_suffix) DO UPDATE SET
  page_type = EXCLUDED.page_type,
  broker_name = EXCLUDED.broker_name,
  account_session_id = EXCLUDED.account_session_id,
  total_assets = EXCLUDED.total_assets,
  total_pnl = EXCLUDED.total_pnl,
  total_pnl_today = EXCLUDED.total_pnl_today,
  total_pnl_today_pct = EXCLUDED.total_pnl_today_pct,
  market_value = EXCLUDED.market_value,
  available_cash = EXCLUDED.available_cash,
  withdrawable_cash = EXCLUDED.withdrawable_cash,
  position_pct = EXCLUDED.position_pct,
  holding_count = EXCLUDED.holding_count,
  active_holding_count = EXCLUDED.active_holding_count,
  source_image_path = EXCLUDED.source_image_path,
  source_image_sha256 = EXCLUDED.source_image_sha256,
  raw_vlm_json = EXCLUDED.raw_vlm_json,
  vlm_model = EXCLUDED.vlm_model,
  vlm_confidence = EXCLUDED.vlm_confidence,
  needs_review = EXCLUDED.needs_review,
  notes = EXCLUDED.notes
RETURNING id;
"""

ITEM_INSERT = """
INSERT INTO strategy_mining.holding_items
  (snapshot_id, trade_date, account_session_id, row_index,
   stock_name_visible, stock_name_obfuscation, stock_name_full,
   symbol, exchange, match_confidence, match_method,
   market_value, pnl_amount, pnl_pct,
   position_qty, available_qty, cost_price, current_price,
   is_zero_position, needs_review, notes)
VALUES (%s,%s,%s,%s, %s,%s,%s, %s,%s,%s,%s, %s,%s,%s, %s,%s,%s,%s, %s,%s,%s)
ON CONFLICT (snapshot_id, row_index) DO UPDATE SET
  stock_name_visible = EXCLUDED.stock_name_visible,
  stock_name_obfuscation = EXCLUDED.stock_name_obfuscation,
  stock_name_full = EXCLUDED.stock_name_full,
  symbol = EXCLUDED.symbol,
  exchange = EXCLUDED.exchange,
  match_confidence = EXCLUDED.match_confidence,
  match_method = EXCLUDED.match_method,
  market_value = EXCLUDED.market_value,
  pnl_amount = EXCLUDED.pnl_amount,
  pnl_pct = EXCLUDED.pnl_pct,
  position_qty = EXCLUDED.position_qty,
  available_qty = EXCLUDED.available_qty,
  cost_price = EXCLUDED.cost_price,
  current_price = EXCLUDED.current_price,
  is_zero_position = EXCLUDED.is_zero_position,
  needs_review = EXCLUDED.needs_review,
  notes = EXCLUDED.notes;
"""


def save_snapshot(snap: SnapshotRow) -> int:
    """Upsert a snapshot and its items. Returns the snapshot id."""
    with connection_scope() as conn, conn.cursor() as cur:
        cur.execute(
            SNAPSHOT_UPSERT,
            (
                snap.trader_alias, snap.trade_date, snap.page_type,
                snap.broker_name, snap.account_suffix, snap.account_session_id,
                snap.total_assets, snap.total_pnl, snap.total_pnl_today,
                snap.total_pnl_today_pct, snap.market_value,
                snap.available_cash, snap.withdrawable_cash,
                snap.position_pct, snap.holding_count, snap.active_holding_count,
                snap.source_image_path, snap.source_image_sha256,
                Json(snap.raw_vlm_json), snap.vlm_model, snap.vlm_confidence,
                snap.needs_review, snap.notes,
            ),
        )
        snapshot_id = cur.fetchone()[0]

        # Clear existing items to keep row_index stable across re-runs
        cur.execute(
            "DELETE FROM strategy_mining.holding_items WHERE snapshot_id = %s",
            (snapshot_id,),
        )
        for it in snap.items:
            cur.execute(
                ITEM_INSERT,
                (
                    snapshot_id, snap.trade_date, snap.account_session_id,
                    it.row_index,
                    it.stock_name_visible, it.stock_name_obfuscation,
                    it.stock_name_full, it.symbol, it.exchange,
                    it.match_confidence, it.match_method,
                    it.market_value, it.pnl_amount, it.pnl_pct,
                    it.position_qty, it.available_qty,
                    it.cost_price, it.current_price,
                    it.is_zero_position, it.needs_review, it.notes,
                ),
            )
    logger.info("saved snapshot id=%s with %d items", snapshot_id, len(snap.items))
    return snapshot_id


def list_snapshots(
    trader_alias: str,
    *, page_type: str | None = "position_page",
) -> list[dict[str, Any]]:
    sql = """
      SELECT trade_date, broker_name, account_suffix, account_session_id,
             total_assets, position_pct, active_holding_count, vlm_confidence
        FROM strategy_mining.holding_snapshots
       WHERE trader_alias = %s
    """
    params: list[Any] = [trader_alias]
    if page_type:
        sql += " AND page_type = %s"
        params.append(page_type)
    sql += " ORDER BY trade_date;"

    with connection_scope(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
