"""行情数据质量 — 开仓闸的唯一判定入口。"""
from __future__ import annotations

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


def classify_market_quality(
    *,
    is_mock: bool,
    has_sectors: bool,
    hot_sectors: list[str] | None,
    ak_degraded: bool = False,
    hot_source: str | None = None,
) -> dict[str, str]:
    if is_mock:
        quality = QUALITY_MOCK
    elif ak_degraded:
        quality = QUALITY_DEGRADED
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
