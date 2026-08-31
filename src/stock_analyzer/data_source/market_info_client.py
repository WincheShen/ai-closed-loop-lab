"""market-information-platform 的 HTTP 客户端适配层。

只通过 API 访问，不导入 market-information-platform 的代码/库。
"""
from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class MarketInfoClient:
    """连接 market-information-platform 的纯 HTTP 客户端。"""

    def __init__(
        self,
        base_url: str,
        timeout: int = 15,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def is_configured(self) -> bool:
        return bool(self.base_url)

    def _get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        resp = self._session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def fetch_snapshot(self) -> dict[str, Any]:
        """GET /api/market/snapshot + /api/market/sectors，返回原始结构。"""
        snapshot = self._get("/api/market/snapshot")
        try:
            sectors = self._get("/api/market/sectors")
        except Exception as e:  # noqa: BLE001
            logger.warning("MarketInfoClient sectors 失败: %s，使用空板块", e)
            sectors = {"sectors": []}
        sector_items = [
            item for item in sectors.get("sectors", [])
            if item.get("sector_name") != "no_sector"
        ]
        quality = snapshot.get("data_quality", "unknown")
        sector_quality = sectors.get("data_quality", "unknown")
        if quality in {"stale", "degraded", "mock", "unavailable"}:
            combined_quality = quality
        elif sector_quality in {"stale", "degraded", "mock", "unavailable"}:
            combined_quality = sector_quality
        elif not sector_items or sector_quality == "no_sector":
            combined_quality = "no_sector"
        else:
            combined_quality = "full"
        return {
            "stocks": snapshot.get("items", []),
            "sectors": sector_items,
            "meta": {
                "is_mock": snapshot.get("is_mock", False),
                "source": snapshot.get("source", "market_info"),
                "data_quality": combined_quality,
                "fetched_at": snapshot.get("fetched_at", ""),
            },
        }

    def fetch_kline(self, symbol: str, period: str = "daily") -> dict[str, Any]:
        """GET /api/market/kline/{symbol}?period=...，返回原始结构。"""
        data = self._get(f"/api/market/kline/{symbol}?period={period}")
        return {
            "bars": data.get("bars", []),
            "meta": {
                "symbol": data.get("symbol", symbol),
                "period": data.get("period", period),
                "is_mock": data.get("is_mock", False),
                "source": data.get("source", "market_info"),
                "data_quality": data.get("data_quality", "unknown"),
                "fetched_at": data.get("fetched_at", ""),
            },
        }

    def active_interventions(self) -> list[dict[str, Any]]:
        """GET /api/intervention/active，返回活跃的干预列表。"""
        data = self._get("/api/intervention/active")
        if not isinstance(data, list):
            return []
        return data
