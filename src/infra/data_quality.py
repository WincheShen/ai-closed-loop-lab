"""行情数据质量 — 开仓闸的唯一判定入口。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

QUALITY_FULL = "full"
QUALITY_NO_SECTOR = "no_sector"
QUALITY_DEGRADED = "degraded"
QUALITY_MOCK = "mock"
QUALITY_UNAVAILABLE = "unavailable"

SOURCE_EASTMONEY = "eastmoney"
SOURCE_EMAPPDATA = "emappdata"
SOURCE_SINA_INFER = "sina_infer"
SOURCE_NONE = "none"

HOT_SECTOR_STRATEGIES = frozenset({"hot_sector_pullback"})

# mock 沿用真实 regime 的最长间隔（交易日）。超过则强制 defend，禁止再当作战计划。
REGIME_REUSE_MAX_TRADING_DAYS = 2


def classify_market_quality(
    *,
    is_mock: bool,
    has_sectors: bool,
    hot_sectors: list[str] | None,
    ak_degraded: bool = False,
    hot_source: str | None = None,
    upstream_quality: str | None = None,
) -> dict[str, str]:
    # 东财挂了但快照是真票（新浪）→ no_sector/full，不打 degraded。
    # degraded 只表示没有真实行情（mock）。ak_degraded 仅在 mock 时抬成 degraded。
    if upstream_quality in {QUALITY_DEGRADED, QUALITY_MOCK, QUALITY_UNAVAILABLE, "stale"}:
        quality = QUALITY_DEGRADED if upstream_quality == "stale" else upstream_quality
    elif is_mock:
        quality = QUALITY_DEGRADED if ak_degraded else QUALITY_MOCK
    elif not has_sectors and not (hot_sectors or []):
        quality = QUALITY_NO_SECTOR
    else:
        quality = QUALITY_FULL
    if not hot_source:
        if is_mock:
            hot_source = SOURCE_NONE
        elif has_sectors:
            hot_source = SOURCE_EASTMONEY
        elif hot_sectors:
            hot_source = SOURCE_EMAPPDATA
        else:
            hot_source = SOURCE_NONE
    return {"data_quality": quality, "hot_sectors_source": hot_source}


def blocks_new_entries(regime: dict[str, Any] | None) -> bool:
    """mock / degraded / unavailable → 禁止新开仓。"""
    if not regime:
        return False
    q = regime.get("data_quality") or ""
    if q in {QUALITY_MOCK, QUALITY_DEGRADED, QUALITY_UNAVAILABLE}:
        return True
    if regime.get("is_mock_data"):
        return True
    return False


def strategy_blocked_by_quality(strategy_id: str, regime: dict[str, Any] | None) -> bool:
    if blocks_new_entries(regime):
        return True
    if not regime:
        return False
    q = regime.get("data_quality") or ""
    hot = regime.get("hot_sectors") or []
    if strategy_id in HOT_SECTOR_STRATEGIES and (
        q == QUALITY_NO_SECTOR or not hot
    ):
        return True
    return False


def trading_days_between(start: date, end: date) -> int:
    """start 之后到 end（含）的工作日数。周末不计。"""
    if end <= start:
        return 0
    n = 0
    cur = start + timedelta(days=1)
    while cur <= end:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


def parse_trade_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def regime_reuse_expired(
    trade_date: str | None,
    today: date | None = None,
    *,
    max_trading_days: int = REGIME_REUSE_MAX_TRADING_DAYS,
) -> bool:
    """沿用的真实 regime 是否超过 TTL。日期无法解析视为过期。"""
    src = parse_trade_date(trade_date)
    if src is None:
        return True
    return trading_days_between(src, today or date.today()) > max_trading_days
