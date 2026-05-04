"""Backtest Engine — 实战复盘与绩效归因。

职责：
1. 每周末对比 "AI预测结果" vs "市场真实表现"
2. 错误归因：选股逻辑失效？还是交易规则问题？
3. 生成 PerformanceRecord 存入 Central Brain
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from src.central_brain import get_central_brain
from src.graph.state import PerformanceRecord, TradingState
from src.infra.config import cfg
from src.infra.logger import get_agent_logger

logger = get_agent_logger("feedback_loop", "init")


class BacktestEngine:
    """复盘引擎。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.logger = get_agent_logger("feedback_loop", session_id)
        self.brain = get_central_brain()

    def run_backtest(
        self,
        signals: list[dict],
        week_ending: str | None = None,
    ) -> list[PerformanceRecord]:
        """对本周所有信号进行复盘。

        Args:
            signals: 来自 Central Brain 的 trade_signals 列表
            week_ending: 复盘周结束日期 (YYYY-MM-DD)，默认今天
        """
        week = week_ending or datetime.now().strftime("%Y-%m-%d")
        self.logger.info("开始周复盘 — week_ending=%s, 信号数=%d", week, len(signals))

        records: list[PerformanceRecord] = []
        for sig in signals:
            record = self._analyze_single_signal(sig, week)
            records.append(record)

        # 汇总统计
        wins = sum(1 for r in records if r["actual_return"] > 0)
        losses = sum(1 for r in records if r["actual_return"] < 0)
        avg_return = sum(r["actual_return"] for r in records) / len(records) if records else 0

        self.logger.info(
            "复盘完成 — 信号 %d, 盈利 %d, 亏损 %d, 平均收益 %.2f%%",
            len(records), wins, losses, avg_return * 100,
        )

        self.brain.log_agent_event(
            self.session_id,
            "feedback_loop",
            "backtest_complete",
            {
                "week": week,
                "total_signals": len(records),
                "wins": wins,
                "losses": losses,
                "avg_return_pct": round(avg_return * 100, 2),
            },
        )
        return records

    def _analyze_single_signal(self, signal: dict, week: str) -> PerformanceRecord:
        """分析单条信号的实际表现。"""
        symbol = signal["symbol"]
        entry = signal.get("entry_price", 0)
        target = signal.get("target_price", 0)
        stop = signal.get("stop_loss", 0)
        strategy = signal.get("strategy", "unknown")

        # TODO: 接入真实行情数据，计算 signal 发出后的实际走势
        # 占位：基于随机模拟
        import random

        predicted_return = (target - entry) / entry if entry > 0 else 0
        # 模拟实际收益：有 40% 概率触及止损，30% 概率达到目标，30% 介于之间
        rand = random.random()
        if rand < 0.4:
            actual_return = (stop - entry) / entry  # 止损
        elif rand < 0.7:
            actual_return = predicted_return  # 达到目标
        else:
            actual_return = random.uniform(-0.02, predicted_return)

        # 归因分析
        if actual_return < -0.03:
            error_source: Literal["stock_selection", "trading_rule", "market_unexpected", "execution_slippage"] | None = "trading_rule"
            analysis = f"策略 {strategy} 触发后股价下行，触及止损 {stop:.2f}。需检查该策略近期胜率。"
        elif actual_return < 0:
            error_source = "market_unexpected"
            analysis = f"市场整体回调导致 {symbol} 未达预期，属系统性风险。"
        else:
            error_source = None
            analysis = f"{symbol} 按预期运行，策略 {strategy} 有效。"

        record: PerformanceRecord = {
            "record_id": f"REC-{uuid.uuid4().hex[:8].upper()}",
            "signal_id": signal["signal_id"],
            "symbol": symbol,
            "predicted_return": round(predicted_return, 4),
            "actual_return": round(actual_return, 4),
            "holding_days": random.randint(1, 5),
            "error_source": error_source,
            "analysis": analysis,
            "week_ending": week,
        }
        return record

    def error_breakdown(self, records: list[PerformanceRecord]) -> dict[str, Any]:
        """错误归因统计。"""
        errors = [r for r in records if r["error_source"] is not None]
        breakdown = {
            "total_signals": len(records),
            "error_signals": len(errors),
            "by_source": {},
            "by_strategy": {},
        }
        for r in errors:
            src = r["error_source"] or "unknown"
            breakdown["by_source"][src] = breakdown["by_source"].get(src, 0) + 1

        # 按策略统计
        # TODO: 关联 signal.strategy
        return breakdown


async def run_weekly_feedback_node(state: TradingState) -> dict[str, Any]:
    """LangGraph 节点函数 — 每周复盘。

    输入：含 performance_log, trade_signals 的 TradingState
    输出：{"performance_log": [...], "error_analysis": [...]}
    """
    session_id = state["session_id"]
    engine = BacktestEngine(session_id)

    # 从 Central Brain 读取本周所有信号
    signals = state.get("trade_signals", [])
    if not signals:
        # 尝试从数据库读取历史信号
        signals = get_central_brain().store.list_active_signals(session_id)
        # 如果状态中没有，读取全部
        if not signals:
            signals = get_central_brain().store.list_active_signals()

    if not signals:
        return {
            "performance_log": state.get("performance_log", []),
            "error_analysis": state.get("error_analysis", []),
            "logs": state.get("logs", []) + ["[FeedbackLoop] 本周无信号，跳过复盘"],
        }

    records = engine.run_backtest(signals)
    breakdown = engine.error_breakdown(records)

    return {
        "performance_log": state.get("performance_log", []) + records,
        "error_analysis": state.get("error_analysis", []) + [breakdown],
        "logs": state.get("logs", []) + [f"[FeedbackLoop] 复盘 {len(records)} 条信号"],
    }
