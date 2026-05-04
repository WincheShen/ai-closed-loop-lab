"""End-to-end ingestion pipeline for a single screenshot.

    image file -> copy to data dir + sha256
              -> VLM extraction
              -> stock resolver per holding
              -> SnapshotRow + ItemRow
              -> write to PostgreSQL
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import snapshot_repo, stock_resolver, vlm_extractor

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = _PROJECT_ROOT / "data" / "strategy_mining" / "holdings"


def _parse_date_from_filename(path: Path) -> date:
    """Filenames are `YYYY-MM-DD.<ext>`; tolerate compact YYYYMMDD too."""
    stem = path.stem
    for pattern in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(stem, pattern).date()
        except ValueError:
            continue
    m = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", stem)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    raise ValueError(f"cannot parse date from filename: {path.name}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _account_session_id(broker: str | None, suffix: str | None) -> str | None:
    if not broker and not suffix:
        return None
    return f"{broker or '?'}_{suffix or '?'}"


def _copy_image(src: Path, trader_alias: str, trade_date: date,
                data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    dest_dir = data_dir / trader_alias
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{trade_date.isoformat()}{src.suffix.lower()}"
    if not dest.exists() or _sha256(src) != _sha256(dest):
        shutil.copy2(src, dest)
    return dest


# ---------------------------------------------------------------------------
def _resolve_holding(
    holding: dict[str, Any],
    trade_date: date,
) -> tuple[stock_resolver.ResolveResult, bool]:
    """Run resolver; return (result, needs_review)."""
    r = stock_resolver.resolve(
        holding.get("stock_name_visible", ""),
        trade_date=trade_date,
        current_price=holding.get("current_price"),
    )
    needs_review = r.symbol is None or r.confidence < 0.8
    return r, needs_review


def _build_snapshot_row(
    image_path: Path,
    *, trader_alias: str, trade_date: date,
    vlm: vlm_extractor.VLMResult,
    copied_path: Path,
    image_sha256: str,
) -> snapshot_repo.SnapshotRow:
    summary = vlm.summary or {}
    session_id = _account_session_id(vlm.broker_name, vlm.account_suffix)

    items: list[snapshot_repo.ItemRow] = []
    active_count = 0
    for h in vlm.holdings:
        resolved, needs_review = _resolve_holding(h, trade_date)
        is_zero = bool(h.get("is_zero_position"))
        if not is_zero:
            active_count += 1
        items.append(snapshot_repo.ItemRow(
            row_index=int(h.get("row_index", len(items))),
            stock_name_visible=h.get("stock_name_visible"),
            stock_name_obfuscation=h.get("stock_name_obfuscation"),
            stock_name_full=resolved.full_name,
            symbol=resolved.symbol,
            exchange=resolved.exchange,
            match_confidence=resolved.confidence,
            match_method=resolved.method,
            market_value=h.get("market_value"),
            pnl_amount=h.get("pnl_amount"),
            pnl_pct=h.get("pnl_pct"),
            position_qty=h.get("position_qty"),
            available_qty=h.get("available_qty"),
            cost_price=h.get("cost_price"),
            current_price=h.get("current_price"),
            is_zero_position=is_zero,
            needs_review=needs_review,
            notes=None,
        ))

    return snapshot_repo.SnapshotRow(
        trader_alias=trader_alias,
        trade_date=trade_date,
        page_type=vlm.page_type,
        broker_name=vlm.broker_name,
        account_suffix=vlm.account_suffix,
        account_session_id=session_id,
        total_assets=summary.get("total_assets"),
        total_pnl=summary.get("total_pnl"),
        total_pnl_today=summary.get("total_pnl_today"),
        total_pnl_today_pct=summary.get("total_pnl_today_pct"),
        market_value=summary.get("market_value"),
        available_cash=summary.get("available_cash"),
        withdrawable_cash=summary.get("withdrawable_cash"),
        position_pct=summary.get("position_pct"),
        holding_count=len(vlm.holdings),
        active_holding_count=active_count,
        source_image_path=str(copied_path),
        source_image_sha256=image_sha256,
        raw_vlm_json=vlm.raw_json,
        vlm_model=vlm.model,
        vlm_confidence=vlm.extractor_confidence,
        needs_review=any(it.needs_review for it in items)
                     or vlm.page_type != "position_page",
        notes=vlm.notes,
        items=items,
    )


# ---------------------------------------------------------------------------
def ingest_one(
    image_path: str | Path,
    *, trader_alias: str,
    data_dir: Path = DEFAULT_DATA_DIR,
    trade_date: date | None = None,
    vlm_model: str | None = None,
) -> dict[str, Any]:
    """Ingest a single screenshot; returns a summary dict for logging."""
    src = Path(image_path)
    d = trade_date or _parse_date_from_filename(src)
    copied = _copy_image(src, trader_alias, d, data_dir=data_dir)
    sha256 = _sha256(copied)

    logger.info("VLM extracting %s ...", copied.name)
    vlm = vlm_extractor.extract(copied, model=vlm_model)
    snap = _build_snapshot_row(
        copied, trader_alias=trader_alias, trade_date=d,
        vlm=vlm, copied_path=copied, image_sha256=sha256,
    )
    snap_id = snapshot_repo.save_snapshot(snap)
    return {
        "image": copied.name,
        "snapshot_id": snap_id,
        "trade_date": d.isoformat(),
        "page_type": snap.page_type,
        "broker": snap.broker_name,
        "account_suffix": snap.account_suffix,
        "holdings": len(snap.items),
        "active": snap.active_holding_count,
        "needs_review": snap.needs_review,
    }
