"""Strategist Engine — 决策者核心实现。

职责：
1. 对 Explorer 选出的候选票进行深度体检（技术/基本面/情绪面）
2. 根据交易规则计算买入点、止损位、仓位分配
3. 生成带时间戳的 TradeSignal
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from src.central_brain import get_central_brain
from src.graph.state import StockCandidate, TradeSignal, TradingState
from src.infra.config import cfg
from src.infra.logger import get_agent_logger

logger = get_agent_logger("strategist", "init")


class StrategistEngine:
    """决策者引擎。

    后续可接入 TradingAgents 的分析框架作为子模块。
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.logger = get_agent_logger("strategist", session_id)
        self.brain = get_central_brain()
        self.config = cfg()

    def analyze_candidate(self, candidate: StockCandidate) -> TradeSignal | None:
        """对单只候选票进行深度分析，生成交易信号。"""
        symbol = candidate["symbol"]
        name = candidate["name"]

        # TODO: 接入 TradingAgents 的分析师团队（基本面/技术面/情绪面/新闻面）
        # TODO: 计算真实技术指标：均线、ATR、成交量

        # --- 占位实现：基于候选分数和模拟技术规则 ---
        score = candidate["qlib_score"]

        # 简单规则映射
        if score > 0.88:
            strategy = "15分钟放量突破"
            entry = round(random_uniform(10, 50), 2)
        elif score > 0.75:
            strategy = "20日线回踩不破"
            entry = round(random_uniform(10, 50), 2)
        else:
            strategy = "缩量企稳观察"
            entry = round(random_uniform(10, 50), 2)

        stop_loss = round(entry * (1 - self.config.get("default_stop_loss_pct", 0.05)), 2)
        target = round(entry * 1.08, 2)  # 8% 目标
        position_pct = min(self.config.get("max_position_pct", 0.10), 0.10)

        signal: TradeSignal = {
            "signal_id": f"SIG-{uuid.uuid4().hex[:8].upper()}",
            "symbol": symbol,
            "action": "buy",
            "entry_price": entry,
            "target_price": target,
            "stop_loss": stop_loss,
            "position_pct": position_pct,
            "strategy": strategy,
            "rationale": (
                f"[{name}] Qlib评分 {score:.2f}，"
                f"属于 {candidate['sector']} 热点，"
                f"技术形态符合 {strategy}。"
            ),
            "timestamp": datetime.now().isoformat(),
            "expiry": (datetime.now() + timedelta(days=5)).isoformat(),
        }

        self.logger.info(
            "生成信号 %s | %s %s | 策略=%s | 入场=%.2f | 止损=%.2f | 目标=%.2f",
            signal["signal_id"], symbol, name,
            strategy, entry, stop_loss, target,
        )
        return signal

    def generate_signals(
        self, candidates: list[StockCandidate]
    ) -> list[TradeSignal]:
        """批量生成交易信号，并写入 Central Brain。"""
        signals: list[TradeSignal] = []
        for c in candidates[:20]:  # 只深度分析 Top 20
            sig = self.analyze_candidate(c)
            if sig:
                signals.append(sig)
                self.brain.store.save_trade_signal(self.session_id, sig)

        self.brain.log_agent_event(
            self.session_id,
            "strategist",
            "signals_generated",
            {"count": len(signals), "symbols": [s["symbol"] for s in signals]},
        )
        return signals

    def risk_assessment(self, signals: list[TradeSignal]) -> dict:
        """整体风控评估：集中度、相关性、总仓位。"""
        total_position = sum(s["position_pct"] for s in signals)
        sectors = set()  # 可以从 signal 的 symbol 反查
        assessment = {
            "total_position_pct": round(total_position, 2),
            "signal_count": len(signals),
            "max_single_position": max((s["position_pct"] for s in signals), default=0),
            "risk_level": "high" if total_position > 0.5 else "medium" if total_position > 0.3 else "low",
            "warnings": [],
        }
        if total_position > 0.5:
            assessment["warnings"].append("总仓位超过50%，建议减仓")
        return assessment


def run_strategy_node(state: TradingState) -> dict[str, Any]:
    """LangGraph 节点函数 — 决策者分析。

    输入：含 target_stocks 的 TradingState
    输出：{"trade_signals": [...], "risk_assessment": {...}}
    """
    session_id = state["session_id"]
    engine = StrategistEngine(session_id)

    candidates = state.get("target_stocks", [])
    if not candidates:
        return {
            "trade_signals": [],
            "risk_assessment": {"error": "无候选票输入"},
            "logs": state.get("logs", []) + ["[Strategist] 无候选票，跳过"],
        }

    signals = engine.generate_signals(candidates)
    risk = engine.risk_assessment(signals)

    # 广播信号到 EventBus，供 Executioner 订阅
    for sig in signals:
        get_central_brain().bus.emit_trade_signal(sig)

    return {
        "trade_signals": signals,
        "risk_assessment": risk,
        "logs": state.get("logs", []) + [f"[Strategist] 生成 {len(signals)} 条信号，风控等级={risk['risk_level']}"],
    }


# --- 占位辅助 ---
import random

def random_uniform(a: float, b: float) -> float:
    return random.uniform(a, b)
