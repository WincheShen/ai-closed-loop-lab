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
from src.agents.memory.trade_attribution import TradeAttributor
from src.central_brain import get_central_brain
from src.infra.config import cfg
from src.stock_analyzer.data_source.intraday_client import IntradayClient

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
    persona_id: str | None = None,
    force_review_map: dict[str, str] | None = None,
) -> list[dict]:
    """执行一轮盘中复审 + 虚拟交易。

    Args:
        force: 忽略交易时间检查（调试用）
        model_name: 指定LLM模型
        persona_id: 指定人格ID，为None时复审所有持仓
        force_review_map: 可选的 {position_id: reason} 映射，对超期持仓注入强制复审上下文

    Returns:
        本轮所有持仓的复审结果列表
    """
    if not force and not is_trading_hours():
        logger.info("非交易时段，跳过盘中复审")
        return []

    brain = get_central_brain()
    reviewer = PositionReviewAgent(model_name=model_name, persona_id=persona_id)
    session_id = f"intraday-{date.today().isoformat()}-{datetime.now().strftime('%H%M')}-{persona_id or 'all'}"

    # 1. Review all open positions (按persona过滤)
    reviews = reviewer.review_all_positions(force_review_map=force_review_map)
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

    # 4. 检查并执行 pending signals（条件单监控）
    triggered = await _check_pending_signals(brain, session_id, force=force)

    logger.info(
        "盘中复审完成: %d 只持仓, %d 个动作, %d 条件单触发",
        len(reviews), len(actions_taken), len(triggered),
    )
    return action_results + triggered


async def _check_pending_signals(
    brain, session_id: str, force: bool = False,
) -> list[dict]:
    """检查 pending 状态的条件单，价格满足条件时自动执行。

    条件类型:
    - breakout: 当前价 >= entry_price 时触发
    - pullback: 当前价 <= entry_price 时触发

    安全措施:
    - 禁止使用 mock 数据触发条件单（防止随机价格误触发）
    - 当前价偏离入场价 > 30% 视为数据异常，跳过
    - 不覆写原始 entry_price，用它作为下单限价
    """
    pending = brain.store.list_pending_signals()
    if not pending:
        return []

    logger.info("检查 %d 个 pending 条件单", len(pending))
    # 关键: 禁止 mock fallback — 条件单触发必须基于真实行情
    intraday = IntradayClient(allow_mock_fallback=False)
    triggered_results = []

    for sig in pending:
        symbol = sig["symbol"]
        entry_price = sig.get("entry_price", 0)
        condition = sig.get("entry_condition", "breakout")

        # 获取当前价（仅真实数据源，不允许 mock）
        try:
            snapshot = intraday.fetch_intraday_snapshot(symbol, period="1", bar_limit=1)
            current_price = snapshot.current_price or 0
        except Exception as e:
            logger.warning("获取 %s 实时价格失败（数据源不可用）: %s", symbol, e)
            continue

        if current_price <= 0:
            continue

        # 价格合理性校验 — 防止数据源返回异常价格导致误触发
        if entry_price > 0:
            price_deviation = abs(current_price - entry_price) / entry_price
            if price_deviation > 0.30:
                logger.warning(
                    "[SKIP] %s 当前价 %.2f 偏离入场价 %.2f 达 %.0f%%，疑似数据异常，跳过",
                    symbol, current_price, entry_price, price_deviation * 100,
                )
                continue

        # 判断是否触发
        triggered = False
        if condition == "breakout" and current_price >= entry_price:
            triggered = True
            logger.info(
                "[TRIGGERED] %s 突破条件达成: 当前 %.2f >= 入场 %.2f",
                symbol, current_price, entry_price,
            )
        elif condition == "pullback" and current_price <= entry_price:
            triggered = True
            logger.info(
                "[TRIGGERED] %s 回调条件达成: 当前 %.2f <= 入场 %.2f",
                symbol, current_price, entry_price,
            )

        if not triggered:
            logger.debug(
                "[WATCHING] %s | 条件=%s | 入场=%.2f | 当前=%.2f | 未触发",
                symbol, condition, entry_price, current_price,
            )
            continue

        # 触发前重评估 — 检查经验层是否建议取消
        try:
            from src.experience_layer import get_experience
            exp = get_experience()
            persona_id = sig.get("persona_id")

            # 检查个股冷却期
            if exp.is_stock_in_cooldown(symbol):
                logger.info(
                    "[CANCEL] %s 条件单触发但个股在冷却期，取消执行", symbol,
                )
                brain.store.update_signal_status(sig["signal_id"], "cancelled_cooldown")
                continue

            # 检查策略在当前 regime 下是否被暂停
            strategy_id = sig.get("strategy_id", "")
            if strategy_id:
                latest_regime = brain.store.latest_market_regime()
                current_regime = latest_regime.get("regime", "") if latest_regime else ""
                if current_regime:
                    stats = exp.get_strategy_stats(strategy_id, regime=current_regime)
                    if stats and stats.get("recommendation") == "SUSPEND":
                        logger.info(
                            "[CANCEL] %s 条件单触发但策略 %s 在 %s 环境下已暂停 (胜率%.0f%%)，取消执行",
                            symbol, strategy_id, current_regime,
                            stats["win_rate"] * 100,
                        )
                        brain.store.update_signal_status(sig["signal_id"], "cancelled_strategy_suspended")
                        continue
        except Exception:
            logger.warning(
                "条件单重评估异常 %s，安全起见取消执行", symbol, exc_info=True,
            )
            brain.store.update_signal_status(sig["signal_id"], "cancelled_reeval_error")
            continue

        # 触发 → 执行买入
        # 轻量级仓位上限检查 — 弥补条件单路径不经过 RiskGovernor 的缺口
        if persona_id and sig.get("action", "buy") == "buy":
            try:
                from src.agents.risk.risk_governor import RiskGovernor
                from src.agents.cio.trading_persona import get_persona
                persona_obj = get_persona(persona_id=persona_id)
                latest_regime = brain.store.latest_market_regime() or {}
                gov = RiskGovernor(session_id, persona=persona_obj, market_regime=latest_regime)
                used_pct = gov._current_used_pct()
                if used_pct >= gov.max_total_position_pct:
                    logger.info(
                        "[CANCEL] %s 条件单触发但人格 %s 仓位已达上限 (%.0f%% >= %.0f%%)，取消执行",
                        symbol, persona_id, used_pct * 100, gov.max_total_position_pct * 100,
                    )
                    brain.store.update_signal_status(sig["signal_id"], "cancelled_position_limit")
                    continue
            except Exception as e:
                logger.debug("仓位检查异常(非致命): %s", e)

        # 用实时市场价作为成交价（更真实的模拟）
        sig["entry_condition"] = "immediate"
        sig["current_price"] = current_price  # 传递实时价给 executor 的 price guard
        sig["market_price_at_trigger"] = current_price  # 触发时的真实市场价
        engine = ExecutionEngine(session_id, persona_id=persona_id)
        try:
            orders, fills = await engine.monitor_and_execute([sig])
            if fills:
                brain.store.update_signal_status(sig["signal_id"], "filled")
                triggered_results.append({
                    "symbol": symbol,
                    "action": "BUY",
                    "reason": f"条件单触发({condition}): 当前价 {current_price:.2f} 达到入场价 {entry_price:.2f}",
                    "trade_price": fills[0]["avg_price"],
                    "signal_id": sig["signal_id"],
                    "entry_condition": condition,
                })
            else:
                logger.warning("条件单触发但执行失败: %s", symbol)
        except Exception as e:
            logger.error("条件单执行异常 %s: %s", symbol, e)

    # 清理过期的 pending signals
    _expire_stale_pending_signals(brain)

    return triggered_results


def _expire_stale_pending_signals(brain) -> None:
    """将超过有效期的 pending signal 标记为 expired。"""
    conn = brain.store._conn()
    expired = conn.execute(
        """SELECT signal_id, symbol FROM trade_signals
        WHERE status = 'pending'
          AND timestamp IS NOT NULL
          AND datetime(timestamp, '+5 days') < datetime('now')""",
    ).fetchall()
    for row in expired:
        brain.store.update_signal_status(row["signal_id"], "expired")
        logger.info("[EXPIRED] %s 条件单超过5天有效期，已过期", row["symbol"])


async def _execute_review_action(
    brain,
    session_id: str,
    position: dict,
    review: dict,
    persona_id: str | None = None,
) -> dict:
    """根据复审结果执行虚拟交易。"""
    action = review["action"]
    symbol = position["symbol"]
    position_id = position["position_id"]
    current_price = review.get("current_price", position["entry_price"])
    effective_persona_id = persona_id or position.get("persona_id")

    # 强制从数据库读取最新持仓（防止使用过期的 position 对象）
    fresh_position = brain.store.get_position(position_id)
    if fresh_position:
        current_qty = fresh_position.get("current_qty", 0)
        logger.info("[%s] 实时持仓刷新: position_id=%s, current_qty=%d", symbol, position_id, current_qty)
    else:
        current_qty = position.get("current_qty", 0)
        logger.warning("[%s] 无法获取实时持仓，使用传入值: %d", symbol, current_qty)

    mode = cfg().get("trading_mode", "mock")

    result: dict = {"executed": False}

    if action == "ADD":
        # 加仓：固定加 50% 当前持仓（最少100股）
        add_qty = max(int(current_qty * 0.5 // 100) * 100, 100)
        signal = _build_signal(symbol, "buy", current_price, review.get("reason", "复审加仓"))
        engine = ExecutionEngine(session_id, persona_id=effective_persona_id)
        orders, fills = await engine.monitor_and_execute([signal])
        if fills:
            new_qty = current_qty + add_qty
            brain.store.update_position_qty(position_id, new_qty)
            result = {
                "executed": True,
                "trade_side": "buy",
                "trade_qty": add_qty,
                "trade_price": fills[0]["avg_price"],
                "new_qty": new_qty,
            }
            logger.info("[%s] 加仓 %d 股 @ %.2f", symbol, add_qty, fills[0]["avg_price"])

    elif action == "REDUCE":
        # 减仓：卖出 50% 当前持仓（向下取整到100股整数倍）
        reduce_qty = max(int(current_qty * 0.5 // 100) * 100, 100)
        if reduce_qty >= current_qty:
            reduce_qty = current_qty

        logger.info("[%s] REDUCE 计算: current_qty=%d, reduce_qty=%d", symbol, current_qty, reduce_qty)

        # 防止超卖：如果已经卖出过，检查剩余持仓
        if current_qty <= 0:
            logger.warning("[%s] 持仓已为0，跳过减仓", symbol)
            return {**result, "executed": False, "reason": "持仓已为0"}

        signal = _build_signal(symbol, "sell", current_price, review.get("reason", "复审减仓"), qty=reduce_qty)
        logger.info("[%s] REDUCE 信号: target_qty=%s", symbol, signal.get("target_qty"))

        engine = ExecutionEngine(session_id, persona_id=effective_persona_id)
        orders, fills = await engine.monitor_and_execute([signal])
        if fills:
            actual_sold = fills[0]["quantity"]
            new_qty = current_qty - actual_sold
            brain.store.update_position_qty(position_id, new_qty)
            logger.info("[%s] REDUCE 完成: 实际卖出=%d, 新持仓=%d", symbol, actual_sold, new_qty)

            if new_qty <= 0:
                pnl = (current_price - position["entry_price"]) * current_qty
                brain.store.close_position(position_id, current_price, pnl)
                logger.info("[%s] 持仓清零，已关闭 position", symbol)

                # Trigger trade attribution
                try:
                    attributor = TradeAttributor(session_id)
                    attributor.attribute_and_save(position, close_price=current_price)
                except Exception as e:
                    logger.warning("交易归因失败 %s: %s", symbol, e)

            result = {
                "executed": True,
                "trade_side": "sell",
                "trade_qty": actual_sold,
                "trade_price": fills[0]["avg_price"],
                "new_qty": new_qty,
            }
            logger.info("[%s] 减仓 %d 股 @ %.2f", symbol, actual_sold, fills[0]["avg_price"])

    elif action == "EXIT":
        # 清仓
        if current_qty <= 0:
            logger.warning("[%s] 持仓已为0，跳过清仓", symbol)
            return {**result, "executed": False, "reason": "持仓已为0"}

        signal = _build_signal(symbol, "sell", current_price, review.get("reason", "复审清仓"), qty=current_qty)
        logger.info("[%s] EXIT 信号: target_qty=%s", symbol, signal.get("target_qty"))

        engine = ExecutionEngine(session_id, persona_id=effective_persona_id)
        orders, fills = await engine.monitor_and_execute([signal])
        if fills:
            pnl = (current_price - position["entry_price"]) * current_qty
            brain.store.close_position(position_id, current_price, pnl)

            # Trigger trade attribution
            try:
                attributor = TradeAttributor(session_id)
                attributor.attribute_and_save(position, close_price=current_price)
            except Exception as e:
                logger.warning("交易归因失败 %s: %s", symbol, e)

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


def _build_signal(symbol: str, action: str, price: float, reason: str, qty: int = 0) -> dict:
    """构建一个简化的 TradeSignal 供 Executor 执行。

    Args:
        qty: 对 sell 信号，应传入实际卖出股数以避免与持仓脱钩。
    """
    return {
        "signal_id": f"SIG-REV-{uuid.uuid4().hex[:8].upper()}",
        "symbol": symbol,
        "action": action,
        "entry_price": price,
        "target_price": price * 1.05,
        "stop_loss": price * 0.95,
        "position_pct": 0.05,
        "target_qty": qty if qty > 0 else None,
        "strategy": "position_review",
        "rationale": reason,
        "timestamp": datetime.now().isoformat(),
        "expiry": None,
    }
