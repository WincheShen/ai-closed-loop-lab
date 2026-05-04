"""Resolve partially-obfuscated stock names to unique symbols.

Input: a visible name fragment (e.g. '东芯', '广东华', '蓝色光标'),
       the trade_date on the screenshot, and the `current_price` shown
       in the app.

Output: a `ResolveResult` with symbol, exchange, full name and confidence.

Strategy (ordered):
    1. Manual override table (highest priority, used for known curated
       cases from this project).
    2. Prefix match against akshare's full A-share name catalogue.
       -> if unique: confidence = 1.0
       -> if multiple: disambiguate by |close - current_price| / current
                       (<=5% preferred), pick smallest distance.
    3. Fuzzy match (rapidfuzz.partial_ratio) as last resort.
    4. Give up, mark needs_review=True.

Everything is cached per-process to avoid hammering akshare.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from typing import Iterable

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Manual overrides — known stocks appearing in 二池's screenshots
# ---------------------------------------------------------------------------
# key: the visible prefix (first N chars); value: canonical (symbol, exch, full)
MANUAL_PREFIX_MAP: dict[str, tuple[str, str, str]] = {
    # From 3-17 performance page (明文): 东芯股份 sh688110
    "东芯": ("688110", "SH", "东芯股份"),
    "东芯股": ("688110", "SH", "东芯股份"),
    "东芯股份": ("688110", "SH", "东芯股份"),
    # From 4-30 (未涂): 广东华特气体
    "广东华": ("688268", "SH", "广东华特气体"),
    "广东华特气体": ("688268", "SH", "广东华特气体"),
    # From 4-22/4-23 (有时未涂): 蓝色光标, 美诺华
    "蓝色光": ("300058", "SZ", "蓝色光标"),
    "蓝色光标": ("300058", "SZ", "蓝色光标"),
    "美诺": ("603538", "SH", "美诺华"),
    "美诺华": ("603538", "SH", "美诺华"),
    # From 3-31 screenshot: 德明X @ 399.929 cost (可能是德明利)
    # 价格 380-400 元的 "德明X" 只有 德明利 (001309.SZ) 符合
    "德明": ("001309", "SZ", "德明利"),
    "德明利": ("001309", "SZ", "德明利"),
}


@dataclass(frozen=True)
class ResolveResult:
    symbol: str | None
    exchange: str | None
    full_name: str | None
    confidence: float
    method: str                   # exact / manual / prefix+price / prefix / fuzzy / none
    alternatives: list[str]       # other candidates we rejected


# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _name_catalogue() -> pd.DataFrame:
    """Full A-share {symbol, name} catalogue (SH/SZ/BJ), cached per process.

    akshare's ``stock_info_a_code_name`` is nearly static (changes only when
    new IPOs list or stocks rename/delist), so 1-process cache is fine.
    """
    df = ak.stock_info_a_code_name()
    # columns: code, name
    df = df.rename(columns={"code": "symbol", "name": "full_name"})
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    df["exchange"] = df["symbol"].apply(_infer_exchange)
    return df[["symbol", "exchange", "full_name"]].copy()


def _infer_exchange(symbol: str) -> str:
    s = symbol.lstrip("0") or "0"
    if symbol.startswith(("60", "68", "58", "90")):
        return "SH"
    if symbol.startswith(("00", "30", "20")):
        return "SZ"
    if symbol.startswith(("43", "83", "87", "88")):
        return "BJ"
    return "SH"  # fallback; most quality stocks are SH


# ---------------------------------------------------------------------------
@lru_cache(maxsize=512)
def _close_price_on(symbol: str, trade_date: date) -> float | None:
    """Fetch daily close price for one symbol on one date (cached)."""
    d = trade_date.strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date=d, end_date=d,
            adjust="",
        )
    except Exception as exc:  # pragma: no cover — network hiccups
        logger.warning("akshare hist failed for %s @ %s: %s", symbol, d, exc)
        return None
    if df is None or df.empty:
        return None
    # columns vary by akshare version; try common ones
    for col in ("收盘", "close", "收盘价"):
        if col in df.columns:
            return float(df.iloc[-1][col])
    return None


# ---------------------------------------------------------------------------
def resolve(
    stock_name_visible: str,
    *,
    trade_date: date | str | None = None,
    current_price: float | None = None,
    price_tolerance: float = 0.10,
) -> ResolveResult:
    """Resolve a visible name fragment into a canonical symbol.

    Args:
        stock_name_visible: What the VLM saw, e.g. '东芯' or '广东华特气体'.
        trade_date: The date shown on the screenshot (YYYY-MM-DD or date).
        current_price: 'current price' column in the app; used to
            disambiguate when prefix yields multiple candidates.
        price_tolerance: relative tolerance for price match; default 10%
            because app intraday prices can differ from the daily close.
    """
    if not stock_name_visible:
        return ResolveResult(None, None, None, 0.0, "none", [])

    name = stock_name_visible.strip()

    # 1. Manual override
    if name in MANUAL_PREFIX_MAP:
        sym, exch, full = MANUAL_PREFIX_MAP[name]
        return ResolveResult(sym, exch, full, 1.0, "manual", [])

    # Try progressively shorter prefixes against manual map
    for k in range(len(name), 1, -1):
        prefix = name[:k]
        if prefix in MANUAL_PREFIX_MAP:
            sym, exch, full = MANUAL_PREFIX_MAP[prefix]
            # only accept if the visible name is a prefix of the full name
            if full.startswith(name) or name.startswith(prefix):
                return ResolveResult(sym, exch, full, 0.95, "manual", [])

    # 2. Prefix match against akshare catalogue
    df = _name_catalogue()
    # If the full visible name is an exact match (VLM saw everything)
    exact = df[df["full_name"] == name]
    if len(exact) == 1:
        r = exact.iloc[0]
        return ResolveResult(
            r["symbol"], r["exchange"], r["full_name"], 1.0, "exact", []
        )

    candidates = df[df["full_name"].str.startswith(name)]
    if len(candidates) == 1:
        r = candidates.iloc[0]
        return ResolveResult(
            r["symbol"], r["exchange"], r["full_name"], 0.95, "prefix", []
        )

    if len(candidates) > 1 and current_price and trade_date is not None:
        td = _coerce_date(trade_date)
        best: tuple[float, pd.Series] | None = None  # (distance, row)
        alternatives: list[str] = []
        for _, row in candidates.iterrows():
            close = _close_price_on(row["symbol"], td)
            if close is None or close <= 0:
                continue
            dist = abs(close - current_price) / current_price
            alternatives.append(f"{row['symbol']}={row['full_name']}@{close}")
            if dist > price_tolerance:
                continue
            if best is None or dist < best[0]:
                best = (dist, row)
        if best is not None:
            row = best[1]
            conf = max(0.6, 1.0 - best[0] * 2)  # 5% off -> 0.9, 10% off -> 0.8
            return ResolveResult(
                row["symbol"], row["exchange"], row["full_name"],
                round(conf, 3), "prefix+price", alternatives,
            )
        # Nothing close enough
        return ResolveResult(
            None, None, None, 0.0, "prefix+price", alternatives,
        )

    # 3. Fuzzy match — last resort
    try:
        from rapidfuzz import process, fuzz
        hits = process.extract(
            name, df["full_name"].tolist(),
            scorer=fuzz.partial_ratio, limit=5,
        )
        best = [h for h in hits if h[1] >= 85]
        if best:
            top = best[0]
            row = df[df["full_name"] == top[0]].iloc[0]
            return ResolveResult(
                row["symbol"], row["exchange"], row["full_name"],
                round(top[1] / 100 * 0.8, 3), "fuzzy",
                [f"{h[0]}@{h[1]}" for h in best],
            )
    except ImportError:
        logger.debug("rapidfuzz not installed; skipping fuzzy match")

    return ResolveResult(None, None, None, 0.0, "none", [])


def _coerce_date(d: date | str) -> date:
    if isinstance(d, date):
        return d
    return datetime.strptime(d, "%Y-%m-%d").date()


# ---------------------------------------------------------------------------
def resolve_many(
    items: Iterable[dict],
) -> list[ResolveResult]:
    """Batch helper — resolves a list of dicts with keys
    `stock_name_visible`, `trade_date`, `current_price`.
    """
    return [
        resolve(
            it.get("stock_name_visible", ""),
            trade_date=it.get("trade_date"),
            current_price=it.get("current_price"),
        )
        for it in items
    ]
