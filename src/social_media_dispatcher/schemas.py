"""Dispatcher 数据契约。

TopicPayload 是与 Social-media-automation 之间的唯一接口契约。
保持与 SMA `AgentState.task` 兼容（description 单段文本）+ 富 metadata。
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Context (rich metadata; SMA 端可消费 / 也可忽略)
# ---------------------------------------------------------------------------

class StockBriefForSMA(BaseModel):
    """精简版股票推荐，只保留 SMA 创作需要的字段。

    注意：不传具体仓位 / 账户金额 / 精确买入价，由 ai-lab 端预先脱敏。
    """
    symbol_masked: str = Field(..., description="脱敏代码，例如 '60xxxx'")
    name_masked: str = Field(..., description="脱敏名称，例如 'X茅'")
    industry: str
    bucket: Literal["aggressive", "stable", "candidate"]
    change_pct: float
    reasoning: str = Field(..., description="一句话推荐理由（已合规改写）")
    agent_summary: Optional[str] = None


class TradeRecordBrief(BaseModel):
    """脱敏后的沈经理交易记录摘要。"""
    record_id: str
    received_at: datetime
    safe_text: str
    redacted_image_url: Optional[str] = None  # 公网可访问 URL，或 SMA 可读路径


class TopicContext(BaseModel):
    """SMA 创作所需的完整背景信息。"""
    pick_date: Optional[date] = None
    hot_sectors: list[str] = Field(default_factory=list)
    recommendations: list[StockBriefForSMA] = Field(default_factory=list)
    trade_record: Optional[TradeRecordBrief] = None
    extra: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Payload（最终推送给 SMA 的 JSON）
# ---------------------------------------------------------------------------

class TopicPayload(BaseModel):
    """推送给 Social-media-automation 的 topic 任务。

    字段映射：
        account_id, description  → 直接落到 SMA tasks 表
        kind                     → 用于 SMA 端做差异化处理
        context                  → SMA 可选读取，做精确化创作
    """
    account_id: str = Field(..., description="目标 SMA 账号 ID，如 XHS_01")
    kind: Literal["daily_picks", "trade_record", "manual"]
    description: str = Field(..., description="人类可读单段，对应 SMA AgentState.task")
    context: TopicContext = Field(default_factory=TopicContext)
    source: str = Field("ai-closed-loop-lab", description="来源标识")
    dispatched_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------

class DispatchResult(BaseModel):
    success: bool
    sma_task_id: Optional[str] = None
    sma_status: Optional[str] = None
    error: Optional[str] = None
    response_body: dict = Field(default_factory=dict)
