"""TopicRouter — 把 daily_picks / trade_record 组装为 TopicPayload。

合规要点：
- 推送给 SMA 的代码/名称必须脱敏（symbol_masked/name_masked）
- 不传精确买入价、仓位、账户金额
- description 字段用合规口吻（"关注/上车/兑现一些" 等已替换的表述）
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from stock_analyzer.pipelines.daily_scan import DailyPicks, RecommendedStock
from .schemas import (
    StockBriefForSMA,
    TopicContext,
    TopicPayload,
    TradeRecordBrief,
)

logger = logging.getLogger(__name__)


def _mask_symbol(symbol: str) -> str:
    """6 位代码脱敏：保留前 2 位 + 'xxxx'。"""
    if len(symbol) >= 6:
        return symbol[:2] + "xxxx"
    return "xxxxxx"


def _mask_name(name: str) -> str:
    """名称脱敏：首字 + 'X' + 末字。"""
    if not name:
        return "某股"
    if len(name) <= 2:
        return name[0] + "X"
    return f"{name[0]}X{name[-1]}"


def _stock_to_brief(stock: RecommendedStock, bucket: str) -> StockBriefForSMA:
    return StockBriefForSMA(
        symbol_masked=_mask_symbol(stock.symbol),
        name_masked=_mask_name(stock.name),
        industry=stock.industry or "未分类",
        bucket=bucket,  # type: ignore[arg-type]
        change_pct=stock.change_pct,
        reasoning=stock.reasoning or "暂无具体推荐理由",
        agent_summary=stock.agent_summary,
    )


class TopicRouter:
    """组装 TopicPayload。"""

    # ------------------------------------------------------------------
    # 选股推荐 → topic
    # ------------------------------------------------------------------

    def from_daily_picks(
        self,
        picks: DailyPicks,
        account_id: str,
        description_override: Optional[str] = None,
    ) -> TopicPayload:
        recs: list[StockBriefForSMA] = []
        for s in picks.aggressive:
            recs.append(_stock_to_brief(s, "aggressive"))
        for s in picks.stable:
            recs.append(_stock_to_brief(s, "stable"))

        sectors_text = "、".join(picks.hot_sectors[:3]) or "暂无明显主线"
        if recs:
            top_rec = recs[0]
            default_desc = (
                f"今日热门板块：{sectors_text}。"
                f"重点关注 {top_rec.industry} 方向，"
                f"代表标的 {top_rec.name_masked}（{top_rec.bucket}）。"
                f"请基于这些热点和板块逻辑创作小红书笔记，"
                f"分享行情观察与板块解读，不出现具体股票代码与名称。"
            )
        else:
            default_desc = (
                f"今日热门板块：{sectors_text}。"
                f"市场暂无明显主线机会，请创作一篇行情观察笔记，"
                f"重点解读板块轮动与情绪变化。"
            )

        context = TopicContext(
            pick_date=picks.pick_date,
            hot_sectors=picks.hot_sectors,
            recommendations=recs,
        )
        return TopicPayload(
            account_id=account_id,
            kind="daily_picks",
            description=description_override or default_desc,
            context=context,
        )

    # ------------------------------------------------------------------
    # 沈经理交易记录 → topic
    # ------------------------------------------------------------------

    def from_trade_record(
        self,
        record_id: str,
        safe_text: str,
        received_at: datetime,
        account_id: str,
        redacted_image_url: Optional[str] = None,
        description_override: Optional[str] = None,
    ) -> TopicPayload:
        default_desc = (
            f"今日交易笔记：{safe_text}。"
            f"请将这段已脱敏的实盘观察改写成小红书风格的复盘笔记，"
            f"避免出现具体股票代码、名称、仓位与精确价格。"
        )
        trade_brief = TradeRecordBrief(
            record_id=record_id,
            received_at=received_at,
            safe_text=safe_text,
            redacted_image_url=redacted_image_url,
        )
        context = TopicContext(trade_record=trade_brief)
        return TopicPayload(
            account_id=account_id,
            kind="trade_record",
            description=description_override or default_desc,
            context=context,
        )

    # ------------------------------------------------------------------
    # 人工选题 → topic（FR-2.5 二次加工模式）
    # ------------------------------------------------------------------

    def from_manual(
        self,
        topic_text: str,
        account_id: str,
    ) -> TopicPayload:
        return TopicPayload(
            account_id=account_id,
            kind="manual",
            description=topic_text,
            context=TopicContext(),
        )
