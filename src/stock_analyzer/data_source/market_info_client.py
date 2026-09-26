"""market-information-platform 的 HTTP 客户端适配层。

只通过 API 访问，不导入 market-information-platform 的代码/库。
包含 Pydantic 合约校验和 circuit breaker 保护。
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# API 合约模型 — 字段缺失时立刻报错，不静默降级
# ---------------------------------------------------------------------------

class SnapshotContract(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    data_quality: str = "unknown"
    is_mock: bool = False
    source: str = "unknown"
    fetched_at: str = ""

class SectorContract(BaseModel):
    sectors: list[dict[str, Any]] = Field(default_factory=list)
    data_quality: str = "unknown"

class KlineContract(BaseModel):
    bars: list[dict[str, Any]] = Field(default_factory=list)
    symbol: str = ""
    period: str = "daily"
    is_mock: bool = False
    source: str = "unknown"
    data_quality: str = "unknown"
    fetched_at: str = ""


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class _CircuitBreaker:
    """简单熔断器：连续失败 N 次后短路，减少无效等待。"""

    def __init__(self, threshold: int = 3, recovery_seconds: float = 60) -> None:
        self._threshold = threshold
        self._recovery = recovery_seconds
        self._failures = 0
        self._last_failure: float = 0

    def can_attempt(self) -> bool:
        if self._failures < self._threshold:
            return True
        return (time.monotonic() - self._last_failure) >= self._recovery

    def record_success(self) -> None:
        self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1
        self._last_failure = time.monotonic()

    @property
    def is_open(self) -> bool:
        return self._failures >= self._threshold and not self.can_attempt()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class MarketInfoClient:
    """连接 market-information-platform 的纯 HTTP 客户端。"""

    def __init__(
        self,
        base_url: str,
        timeout: int = 60,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._cb = _CircuitBreaker(threshold=3, recovery_seconds=60)

    def is_configured(self) -> bool:
        return bool(self.base_url)

    def _get(self, path: str) -> Any:
        if not self._cb.can_attempt():
            raise ConnectionError(
                "market-info-platform circuit breaker OPEN "
                f"(failures={self._cb._failures})"
            )
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            self._cb.record_success()
            return resp.json()
        except Exception:
            self._cb.record_failure()
            raise

    def fetch_snapshot(self) -> dict[str, Any]:
        """GET /api/market/snapshot + /api/market/sectors，返回原始结构。"""
        raw_snapshot = self._get("/api/market/snapshot")
        snapshot = SnapshotContract.model_validate(raw_snapshot)
        try:
            raw_sectors = self._get("/api/market/sectors")
            sectors = SectorContract.model_validate(raw_sectors)
        except Exception as e:  # noqa: BLE001
            logger.warning("MarketInfoClient sectors 失败: %s，使用空板块", e)
            sectors = SectorContract()
        sector_items = [
            item for item in sectors.sectors
            if item.get("sector_name") != "no_sector"
        ]
        quality = snapshot.data_quality
        sector_quality = sectors.data_quality
        if quality in {"stale", "degraded", "mock", "unavailable"}:
            # snapshot 本身有问题 → 直接传递
            combined_quality = quality
        elif sector_quality in {"stale", "degraded", "mock", "unavailable"}:
            # snapshot 正常但 sectors 不可用 → 降级为 no_sector（不阻止选股）
            combined_quality = "no_sector"
        elif not sector_items or sector_quality == "no_sector":
            combined_quality = "no_sector"
        else:
            combined_quality = "full"
        return {
            "stocks": snapshot.items,
            "sectors": sector_items,
            "meta": {
                "is_mock": snapshot.is_mock,
                "source": snapshot.source,
                "data_quality": combined_quality,
                "fetched_at": snapshot.fetched_at,
            },
        }

    def fetch_kline(self, symbol: str, period: str = "daily") -> dict[str, Any]:
        """GET /api/market/kline/{symbol}?period=...，返回原始结构。"""
        raw = self._get(f"/api/market/kline/{symbol}?period={period}")
        data = KlineContract.model_validate(raw)
        return {
            "bars": data.bars,
            "meta": {
                "symbol": data.symbol or symbol,
                "period": data.period or period,
                "is_mock": data.is_mock,
                "source": data.source,
                "data_quality": data.data_quality,
                "fetched_at": data.fetched_at,
            },
        }

    def active_interventions(self) -> list[dict[str, Any]]:
        """GET /api/intervention/active，返回活跃的干预列表。"""
        data = self._get("/api/intervention/active")
        if not isinstance(data, list):
            return []
        return data
