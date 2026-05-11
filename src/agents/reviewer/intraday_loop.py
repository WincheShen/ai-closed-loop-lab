"""盘中循环 — 每 30 分钟复审持仓并执行交易动作。

核心流程:
  scheduler 触发 → review_all_positions → 对有动作的持仓执行虚拟交易 → 持久化

与现有 daily pipeline 的关系:
  - 早盘 daily_scan → 产出 trade_signals → Executor 建仓 → positions 表
  - 盘中 intraday_loop → 复审 positions → 调整/止盈/止损
  - 收盘 closing_analysis → 汇总当日操作 → 发帖
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, time

from src.agents.executioner.executor import ExecutionEngine
from src.agents.reviewer.position_reviewer import PositionReviewAgent
from src.central_brain import get_central_brain
from src.infra.config import cfg

logger = logging.getLogger(__name__)


def is_trading_hours() -> bool:
    """判断当前是否在A股交易时段（含集合竞价）。"""
    now = datetime.now().time()
    morning = time(9, 15) <= now <= time(11, 30)
    afternoon = time(13, 0) <= now <= time(15, 0)
    # 周末不交易
    if datetime.now().weekday() >= 5:
        return False
    return morning or afternoon


async def run_intraday_review(
    force: bool = False,
    model_name: str | None = None,
) -> list[dict]:
    """执行一轮盘中复审 + 虚拟交易。

    Args:
        force: 忽略交易时间检查（调试用）
        model_name: 指定LLM模型

    Returns:
        本轮所有持仓的复审结果列表
    """
    if not force and not is_trading_hours():
        logger.info("非交易时段，跳过盘中复审")
        return []

    brain = get_central_brain()
    reviewer = PositionReviewAgent(model_name=model_name)
    session_id = f"intraday-{date.today().isoformat()}-{datetime.now().strftime('%H%M')}"

    # 1. Review all open positions
    reviews = reviewer.review_all_positions()
    if not reviews:
        logger.info("无持仓或无复审结果")
        return reviews

    # 2. Execute actions for non-HOLD results
    action_results = []
    for review in reviews:
        action = review.get("action", "HOLD")
        if action == "HOLD":
            action_results.append(review)
            continue

        position_id = review.get("position_id")
        position = brain.store.get_position(position_id) if position_id else None
        if not position:
            logger.warning("Position %s not found, skipping", position_id)
            action_results.append(review)
            continue

        try:
            result = await _execute_review_action(
                brain, session_id, position, review,
            )
            review.update(result)
        except Exception as e:
            logger.error(
                "执行 %s %s 失败: %s",
                review.get("action"), position.get("symbol"), e,
            )
            review["execution_error"] = str(e)

        action_results.append(review)

    # 3. Log summary
    actions_taken = [r for r in action_results if r.get("action") != "HOLD"]
    brain.log_agent_event(
        session_id=session_id,
        agent="reviewer",
        event_type="intraday_review_complete",
        payload={
            "total_positions": len(reviews),
            "actions_taken": len(actions_taken),
            "summary": [
                {"symbol": r["symbol"], "action": r["action"], "reason": r.get("reason", "")}
                for r in actions_taken
            ],
        },
    )

    logger.info(
        "盘中复审完成: %d 只持仓, %d 个动作",
        len(reviews), len(actions_taken),
    )
    return action_results


async def _execute_review_action(
    brain,
    session_id: str,
    position: dict,
    review: dict,
) -> dict:
    """根据复审结果执行虚拟交易。"""
    action = review["action"]
    symbol = position["symbol"]
    current_price = review.get("current_price", position["entry_price"])
    current_qty = position.get("current_qty", 0)
    mode = cfg().get("trading_mode", "mock")

    result: dict = {"executed": False}

    if action == "ADD":
        # 加仓：固定加 50% 当前持仓（最少100股）
        add_qty = max(int(current_qty * 0.5 // 100) * 100, 100)
        signal = _build_signal(symbol, "buy", current_price, review.get("reason", "复审加仓"))
        engine = ExecutionEngine(session_id)
        orders, fills = await engine.monitor_and_execute([signal])
        if fills:
            new_qty = current_qty + add_qty
            brain.store.update_position_qty(position["position_id"], new_qty)
            result = {
                "executed": True,
                "trade_side": "buy",
                "trade_qty": add_qty,
                "trade_price": fills[0]["avg_price"],
                "new_qty": new_qty,
            }
            logger.info("[%s] 加仓 %d 股 @ %.2f", symbol, add_qty, fills[0]["avg_price"])

    elif action == "REDUCE":
        # 减仓：卖出 50% 当前持仓
        reduce_qty = max(int(current_qty * 0.5 // 100) * 100, 100)
        if reduce_qty >= current_qty:
            reduce_qty = current_qty
        signal = _build_signal(symbol, "sell", current_price, review.get("reason", "复审减仓"))
        engine = ExecutionEngine(session_id)
        orders, fills = await engine.monitor_and_execute([signal])
        if fills:
            new_qty = current_qty - reduce_qty
            brain.store.update_position_qty(position["position_id"], new_qty)
            if new_qty <= 0:
                pnl = (current_price - position["entry_price"]) * current_qty
                brain.store.close_position(position["position_id"], current_price, pnl)
            result = {
                "executed": True,
                "trade_side": "sell",
                "trade_qty": reduce_qty,
                "trade_price": fills[0]["avg_price"],
                "new_qty": new_qty,
            }
            logger.info("[%s] 减仓 %d 股 @ %.2f", symbol, reduce_qty, fills[0]["avg_price"])

    elif action == "EXIT":
        # 清仓
        signal = _build_signal(symbol, "sell", current_price, review.get("reason", "复审清仓"))
        engine = ExecutionEngine(session_id)
        orders, fills = await engine.monitor_and_execute([signal])
        if fills:
            pnl = (current_price - position["entry_price"]) * current_qty
            brain.store.close_position(position["position_id"], current_price, pnl)
            result = {
                "executed": True,
                "trade_side": "sell",
                "trade_qty": current_qty,
                "trade_price": fills[0]["avg_price"],
                "new_qty": 0,
                "realized_pnl": round(pnl, 2),
            }
            logger.info("[%s] 清仓 %d 股 @ %.2f, PnL=%.2f", symbol, current_qty, fills[0]["avg_price"], pnl)

    return result


def _build_signal(symbol: str, action: str, price: float, reason: str) -> dict:
    """构建一个简化的 TradeSignal 供 Executor 执行。"""
    return {
        "signal_id": f"SIG-REV-{uuid.uuid4().hex[:8].upper()}",
        "symbol": symbol,
        "action": action,
        "entry_price": price,
        "target_price": price * 1.05,
        "stop_loss": price * 0.95,
        "position_pct": 0.05,
        "strategy": "position_review",
        "rationale": reason,
        "timestamp": datetime.now().isoformat(),
        "expiry": None,
    }
