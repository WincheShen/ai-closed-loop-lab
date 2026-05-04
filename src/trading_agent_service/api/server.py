"""TradingAgent HTTP Service — FastAPI 入口。

启动：
    uvicorn trading_agent_service.api.server:app --port 8001 --reload
或：
    python scripts/run_trading_agent_service.py
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException

from ..analysis import get_analyzer
from ..cache import CacheManager
from .schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ReportMetadata,
    ServiceStats,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------

class _State:
    started_at: float = 0.0
    total_requests: int = 0
    cache_hits: int = 0
    cache_miss: int = 0


_state = _State()

# 数据根目录可通过环境变量覆盖
_DATA_ROOT = Path(os.environ.get("TAS_DATA_ROOT", "data/trading_agent_service"))
_ANALYZER_PREFER = os.environ.get("TAS_ANALYZER", "auto")  # auto | mock | tradingagents

cache = CacheManager(_DATA_ROOT)
analyzer = get_analyzer(_ANALYZER_PREFER)

app = FastAPI(
    title="TradingAgent Service",
    description="单股深度分析 HTTP 服务，对应 AI 闭环实验室 v0.2 模块3",
    version="0.1.0",
)


@app.on_event("startup")
def _on_start() -> None:
    _state.started_at = time.time()
    logger.info(
        "TradingAgent Service started: analyzer=%s data_root=%s",
        analyzer.name,
        _DATA_ROOT,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "analyzer": analyzer.name,
        "uptime_seconds": time.time() - _state.started_at,
    }


@app.get("/stats", response_model=ServiceStats)
def stats() -> ServiceStats:
    total = _state.total_requests
    hits = _state.cache_hits
    return ServiceStats(
        total_requests=total,
        cache_hits=hits,
        cache_miss=_state.cache_miss,
        cache_hit_rate=(hits / total) if total else 0.0,
        cached_reports=cache.total_cached(),
        uptime_seconds=time.time() - _state.started_at,
    )


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    _state.total_requests += 1
    start = time.time()

    # 1. 查缓存
    if not req.force_refresh:
        # current_price 暂未提供时跳过价格区间检查；后续可在此查行情
        result = cache.lookup(req.symbol)
        if result.hit and result.report is not None:
            _state.cache_hits += 1
            logger.info("cache HIT %s: %s", req.symbol, result.reason)
            return AnalyzeResponse(
                symbol=result.report.symbol,
                name=result.report.name,
                report=result.report,
                metadata=ReportMetadata(
                    evaluated_at=datetime.combine(
                        result.report.valid_until, datetime.min.time()
                    ),
                    cache_hit=True,
                    depth=req.depth,
                    knowledge_planet_url=result.knowledge_planet_url,
                    elapsed_seconds=time.time() - start,
                ),
            )
        logger.info("cache MISS %s: %s", req.symbol, result.reason)

    # 2. 真实分析
    _state.cache_miss += 1
    try:
        report = analyzer.analyze(req.symbol, depth=req.depth)
    except Exception as e:  # noqa: BLE001
        logger.exception("analyze failed for %s", req.symbol)
        raise HTTPException(status_code=500, detail=f"analyze failed: {e}") from e

    evaluated_at = datetime.now()
    cache.store(report, evaluated_at=evaluated_at)

    return AnalyzeResponse(
        symbol=report.symbol,
        name=report.name,
        report=report,
        metadata=ReportMetadata(
            evaluated_at=evaluated_at,
            cache_hit=False,
            depth=req.depth,
            knowledge_planet_url=None,  # Phase 3 接入知识星球
            elapsed_seconds=time.time() - start,
        ),
    )


@app.get("/report/{symbol}", response_model=AnalyzeResponse)
def get_report(symbol: str) -> AnalyzeResponse:
    """获取最新缓存报告（不触发新分析）。"""
    result = cache.lookup(symbol)
    if not result.hit or result.report is None:
        raise HTTPException(status_code=404, detail=f"no cached report: {result.reason}")
    return AnalyzeResponse(
        symbol=result.report.symbol,
        name=result.report.name,
        report=result.report,
        metadata=ReportMetadata(
            evaluated_at=datetime.combine(result.report.valid_until, datetime.min.time()),
            cache_hit=True,
            depth="deep",
            knowledge_planet_url=result.knowledge_planet_url,
            elapsed_seconds=0.0,
        ),
    )
