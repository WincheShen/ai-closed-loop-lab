"""TradingAgent Service — Pydantic 数据契约。

对应需求文档 FR-3.4 报告内容规范。
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    symbol: str = Field(..., description="股票代码，如 600519 / 000001 / sh600519")
    force_refresh: bool = Field(False, description="跳过缓存，强制重新分析")
    depth: Literal["deep", "quick"] = Field("deep", description="分析深度")
    requested_by: Optional[str] = Field(None, description="调用方标识，用于审计")


# ---------------------------------------------------------------------------
# Report payload
# ---------------------------------------------------------------------------

class TechnicalAnalysis(BaseModel):
    trend: str = Field(..., description="趋势描述：上行/震荡/下行")
    key_levels: dict[str, float] = Field(default_factory=dict, description="支撑/阻力等关键位")
    summary: str = ""


class FundamentalAnalysis(BaseModel):
    industry: str = ""
    market_cap_yi: Optional[float] = Field(None, description="市值（亿元）")
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    roe: Optional[float] = None
    summary: str = ""


class Report(BaseModel):
    """单股深度分析报告。"""

    symbol: str
    name: str
    current_price: float

    summary: str = Field(..., description="一段话核心结论")
    technical: TechnicalAnalysis
    fundamental: FundamentalAnalysis

    bull_case: str = Field(..., description="多方观点（来自 TradingAgents 辩论）")
    bear_case: str = Field(..., description="空方观点")
    final_decision: Literal["BUY", "HOLD", "SELL"]
    confidence: float = Field(..., ge=0.0, le=1.0)

    # ⭐ 缓存失效判定的关键字段
    reevaluation_price_range: tuple[float, float] = Field(
        ..., description="再评估价格区间 [下界, 上界]，超出则缓存失效"
    )
    valid_until: date = Field(..., description="报告失效日期（按日粒度）")

    risk_warning: str = ""


class ReportMetadata(BaseModel):
    evaluated_at: datetime
    cache_hit: bool
    depth: Literal["deep", "quick"]
    knowledge_planet_url: Optional[str] = None
    elapsed_seconds: float = 0.0


class AnalyzeResponse(BaseModel):
    symbol: str
    name: str
    report: Report
    metadata: ReportMetadata


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class ServiceStats(BaseModel):
    total_requests: int
    cache_hits: int
    cache_miss: int
    cache_hit_rate: float
    cached_reports: int
    uptime_seconds: float
