"""TradingState — 统一状态定义，贯穿四大 Agent 簇的全生命周期。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional, TypedDict


class StockCandidate(TypedDict):
    """探索者筛选出的候选股票。"""

    symbol: str                    # 股票代码 (如 "600519.SH")
    name: str                      # 股票名称
    qlib_score: float              # Qlib Alpha 预测分数
    sector: str                    # 所属板块
    hot_reason: list[str]          # 热点关联理由
    kline_summary: dict            # K线形态摘要
    fund_flow: Optional[dict]      # 资金流向
    dragon_tiger: Optional[dict]   # 龙虎榜数据


class TradeSignal(TypedDict):
    """决策者生成的交易信号。"""

    signal_id: str                 # 唯一ID
    symbol: str
    action: Literal["buy", "sell", "hold"]
    entry_price: float             # 买入价/触发价
    target_price: float            # 目标价
    stop_loss: float               # 止损价
    position_pct: float            # 建议仓位占比 (如 0.1 = 10%)
    strategy: str                  # 触发策略 (如 "20日线回踩", "15分钟放量突破")
    rationale: str                 # 决策理由
    timestamp: str                 # ISO 格式时间戳
    expiry: Optional[str]          # 信号有效期


class Order(TypedDict):
    """执行者层面的订单。"""

    order_id: str
    signal_id: str                 # 关联的 TradeSignal
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    order_type: Literal["market", "limit", "stop"]
    limit_price: Optional[float]
    status: Literal["pending", "submitted", "partial", "filled", "cancelled", "rejected"]
    submitted_at: Optional[str]
    updated_at: Optional[str]


class Fill(TypedDict):
    """成交回传记录。"""

    fill_id: str
    order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    avg_price: float
    fees: float
    filled_at: str


class Post(TypedDict):
    """社交媒体发布记录。"""

    post_id: str
    platform: Literal["xiaohongshu", "douyin", "wechat"]
    title: str
    content: str
    images: list[str]              # 本地图片路径
    url: Optional[str]             # 发布后的链接
    published_at: str
    trade_summary: Optional[dict]  # 关联的交易摘要 (脱敏)


class Comment(TypedDict):
    """社交媒体评论/粉丝反馈。"""

    comment_id: str
    post_id: str
    platform: Literal["xiaohongshu", "douyin", "wechat"]
    author: str
    content: str
    likes: int
    quality_score: float           # AI评估的高质量分数 (0-1)
    extracted_insight: Optional[str]  # AI提炼的投资观点
    created_at: str


class PerformanceRecord(TypedDict):
    """单条绩效记录，用于复盘。"""

    record_id: str
    signal_id: str
    symbol: str
    predicted_return: float        # AI预测收益
    actual_return: float           # 实际收益
    holding_days: int
    error_source: Optional[Literal["stock_selection", "trading_rule", "market_unexpected", "execution_slippage"]]
    analysis: str                  # 归因分析文字
    week_ending: str               # 所属复盘周


class PromptWeight(TypedDict):
    """策略 Prompt 的权重配置。"""

    strategy_name: str             # 如 "20日线回踩"
    current_weight: float          # 当前权重 (0-1)
    win_count: int
    loss_count: int
    last_updated: str


# =============================================================================
# 核心统一状态 — LangGraph 全局状态机
# =============================================================================

class TradingState(TypedDict):
    """Central Brain 维护的全局状态。

    所有 Agent 簇通过读写此状态进行协作。
    空字段表示该阶段尚未产出数据。
    """

    # --- 元信息 ---
    session_id: str                # 本次运行会话ID
    run_mode: Literal["scan", "paper", "live"]  # 运行模式
    timestamp: str                   # 状态最后更新时间

    # ===== Phase 0: 认知层 (MarketBrain + Persona) =====
    market_regime: Optional[dict]    # MarketRegimeSnapshot 序列化
    persona_version: Optional[str]   # 当前生效的 TradingPersona 版本

    # ===== Phase 1: 探索 (Explorer) =====
    hot_sectors: list[str]           # 热门板块列表
    target_stocks: list[StockCandidate]  # Qlib 选出的候选票
    social_sentiment: dict           # {sector: sentiment_score}

    # ===== Phase 2: 决策 (Strategist) =====
    trade_signals: list[TradeSignal]  # 生成的交易信号
    risk_assessment: dict            # 整体风控评估
    risk_decisions: list[dict]       # RiskGovernor 的 approve/reduce/reject 记录
    portfolio_status: Optional[dict]  # 当前持仓快照

    # ===== Phase 3: 执行 (Executioner) =====
    active_orders: list[Order]       # 待执行/已提交订单
    filled_orders: list[Fill]       # 已成交记录
    market_data: Optional[dict]      # 实时行情缓存

    # ===== Phase 4: 传播 (Influencer) =====
    published_posts: list[Post]     # 已发布内容
    fan_feedback: list[Comment]     # 粉丝评论反馈

    # ===== Phase 5: 进化 (Feedback Loop) =====
    performance_log: list[PerformanceRecord]  # 历史绩效
    prompt_weights: list[PromptWeight]        # 策略权重表
    error_analysis: list[dict]       # 错误归因日志

    # --- 控制流 ---
    next_node: Optional[str]         # 下一个要执行的节点 (用于条件路由)
    errors: list[dict]               # 错误收集桶
    logs: list[str]                  # 运行日志摘要


def create_empty_state(session_id: str, run_mode: str = "scan") -> TradingState:
    """创建一个新的空状态实例。"""
    return {
        "session_id": session_id,
        "run_mode": run_mode,  # type: ignore[typeddict-item]
        "timestamp": datetime.now().isoformat(),
        "market_regime": None,
        "persona_version": None,
        "hot_sectors": [],
        "target_stocks": [],
        "social_sentiment": {},
        "trade_signals": [],
        "risk_assessment": {},
        "risk_decisions": [],
        "portfolio_status": None,
        "active_orders": [],
        "filled_orders": [],
        "market_data": None,
        "published_posts": [],
        "fan_feedback": [],
        "performance_log": [],
        "prompt_weights": [],
        "error_analysis": [],
        "next_node": None,
        "errors": [],
        "logs": [],
    }
