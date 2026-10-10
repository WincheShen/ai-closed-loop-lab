"""尾盘狙击 (14:35) 与早盘脉冲 (09:35) 极速动量执行引擎。

核心设计：
1. 14:35 尾盘狙击 (execute_tail_hunt)：
   - 冷静期闸门：若前一交易日出现止损亏损 (<= -2.5%) 或当日全市场上涨比例 < 30%，强制全天 0 仓位空仓（复刻 9/15 纪律）；
   - 从 ShortTermWatchlistManager 动态自选股池中筛选标的，拉取 14:30 盘中分钟/快照行情；
   - 匹配动量延续或强势企稳形态，挑出最强 1-2 只建仓（严格代码规则，零 LLM 编造价格）；
   - 纳入数据防护，绝不接受 mock 数据或异常偏离价格。
2. 09:35 早盘脉冲 (execute_morning_pulse)：
   - 次日早盘 9:30-10:00 脉冲冲高（浮盈 >= 3%~8%）果断兑现；
   - 低开破位（浮亏 <= -2.5% 或跌破止损）毫不犹豫斩仓；
   - 平仓后若中期趋势健康，自动回流至自选股观察池（复刻行云科技二次狙击）。
3. 14:30 次日强制清仓 (execute_force_close_ultra_short)：
   - 超过 1 个交易日（T+1）未退出的持仓在 14:30 无条件清仓，坚决不和股票谈恋爱。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime
from typing import Any

from src.agents.executioner.executor import ExecutionEngine
from src.agents.explorer.short_term_watchlist import ShortTermWatchlistManager
from src.agents.reviewer.intraday_loop import _execute_review_action
from src.central_brain import get_central_brain
from src.infra.notify import notify_ops
from src.stock_analyzer.data_source.intraday_client import IntradayClient
from src.stock_analyzer.detectors.ultra_short_momentum import (
    STRATEGY_ID,
    _CONT_CLOSE_POS,
    _MIN_AMPLITUDE,
    _MIN_TURNOVER,
    _MIN_UP_RATIO,
)

logger = logging.getLogger(__name__)

PERSONA_ID = "ultra_short_momentum_v1"


def check_cold_period(brain, persona_id: str = PERSONA_ID) -> tuple[bool, str]:
    """检查是否处于大亏冷静期或恶劣天气强制空仓期。

    Returns:
        (is_cold, reason): True 表示触发冷静期/恶劣天气，禁止开新仓。
    """
    store = brain.store

    # 1. 检查最近一笔平仓交易是否大亏（<= -2.5%）
    try:
        closed_positions = store.list_positions(persona_id=persona_id, status="closed")
        if closed_positions:
            # 按 closed_at 倒序排序
            sorted_closed = sorted(
                closed_positions,
                key=lambda p: p.get("closed_at") or "",
                reverse=True,
            )
            latest = sorted_closed[0]
            closed_at_str = latest.get("closed_at") or ""
            if closed_at_str:
                closed_date = date.fromisoformat(closed_at_str[:10])
                from src.infra.data_quality import trading_days_between
                days_since_closed = trading_days_between(closed_date, date.today())
                # 如果在 1 个交易日之内（含当日平仓或上个交易日平仓），且亏损 >= 2.5%
                entry_p = float(latest.get("entry_price") or 0)
                close_p = float(latest.get("close_price") or 0)
                pnl_pct = (close_p / entry_p - 1) * 100 if entry_p > 0 else 0
                realized_pnl = float(latest.get("realized_pnl") or 0)

                if days_since_closed <= 1 and (pnl_pct <= -2.5 or realized_pnl < -1000):
                    reason = (
                        f"上一笔交易 {latest.get('symbol')} 于 {closed_at_str[:10]} 触发止损 "
                        f"(PnL={pnl_pct:.2f}%)，进入 1 个交易日强制冷静期，全天保持 100% 现金"
                    )
                    return True, reason
    except Exception as e:  # noqa: BLE001
        logger.warning("检查交易历史冷静期异常: %s", e)

    # 2. 检查当日大盘天气（上涨比例 < 30% 或 panic 状态）
    try:
        conn = store._conn()
        row = conn.execute(
            "SELECT * FROM market_regime_snapshots ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
        if row:
            regime = row["regime"]
            if regime == "panic":
                return True, "市场环境处于恐慌 (regime=panic)，恶劣天气强制 0 仓位"

            evidence_str = (
                row["evidence_json"] if "evidence_json" in row.keys()
                else (row["evidence"] if "evidence" in row.keys() else "{}")
            )
            if evidence_str:
                try:
                    ev = json.loads(evidence_str) if isinstance(evidence_str, str) else evidence_str
                    up_ratio = float(ev.get("up_ratio", -1))
                    if 0 <= up_ratio < _MIN_UP_RATIO:
                        return True, f"今日全市场上涨家数占比仅 {up_ratio:.1%} (低于 30% 警戒线)，恶劣天气强制空仓保命"
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001
        logger.warning("检查大盘天气闸门异常: %s", e)

    return False, ""


async def execute_tail_hunt(
    brain=None,
    persona_id: str = PERSONA_ID,
    session_id: str | None = None,
) -> dict[str, Any]:
    """14:35 尾盘狙击：从自选股池中选择符合条件的标的尾盘建仓。"""
    brain = brain or get_central_brain()
    session_id = session_id or f"tail-hunt-{datetime.now().strftime('%Y%m%d-%H%M')}-{uuid.uuid4().hex[:4]}"
    logger.info("⏰ [TAIL-HUNT] 开始执行 14:35 尾盘狙击 (persona=%s, session=%s)", persona_id, session_id)

    evaluated: list[dict[str, Any]] = []
    pool_size = 0

    def _done(result: dict[str, Any]) -> dict[str, Any]:
        """统一收尾：写 tail_hunt_complete 事件供监控页展示。"""
        payload = dict(result)
        payload["pool_size"] = pool_size
        if evaluated:
            payload["evaluated"] = evaluated
        try:
            brain.log_agent_event(session_id, "tail_hunt", "tail_hunt_complete", payload)
        except Exception:
            logger.debug("tail_hunt_complete 事件写入失败", exc_info=True)
        return result

    # 1. 冷静期与大盘天气前置硬闸门
    is_cold, cold_reason = check_cold_period(brain, persona_id=persona_id)
    if is_cold:
        logger.warning("🚫 [TAIL-HUNT] 闸门拦截: %s", cold_reason)
        return _done({"executed": False, "reason": cold_reason, "action": "SKIP"})

    # 2. 获取短线自选股池
    wl_mgr = ShortTermWatchlistManager(brain)
    watchlist_items = wl_mgr.get_active_pool()
    pool_size = len(watchlist_items)
    if not watchlist_items:
        logger.info("[TAIL-HUNT] 短线自选股池为空，跳过尾盘狙击")
        return _done({"executed": False, "reason": "短线自选股池为空", "action": "SKIP"})

    # 3. 检查资金账户可用资金 (若未开户则自动初始化 30 万模拟子账户)
    account = brain.store.get_account_by_persona(persona_id)
    if not account:
        try:
            brain.store.create_account(
                persona_id=persona_id,
                persona_name="极速动量隔夜客",
                capital=300000.0,
            )
            account = brain.store.get_account_by_persona(persona_id)
            logger.info("[TAIL-HUNT] 自动为 %s 初始化资金账户 acc-%s (30万)", persona_id, persona_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("[TAIL-HUNT] 初始化账户异常: %s", e)

    available_cash = float(account.get("available_cash", 0)) if account else 300000.0
    if available_cash < 10000.0:
        logger.warning("[TAIL-HUNT] 账户可用资金不足 (%.2f < 10000)，跳过建仓", available_cash)
        return _done({"executed": False, "reason": f"可用资金不足 ({available_cash:.2f})", "action": "SKIP"})

    # 检查持仓数量，最多持有 2 只
    open_positions = brain.store.list_open_positions(persona_id=persona_id)
    if len(open_positions) >= 2:
        logger.info("[TAIL-HUNT] 当前已有 %d 只持仓 (上限 2 只)，跳过开新仓", len(open_positions))
        return _done({"executed": False, "reason": "持仓已达上限", "action": "SKIP"})

    # 4. 扫描自选股池，拉取 14:30 盘中真实快照
    intraday = IntradayClient(allow_mock_fallback=False)
    candidates_scored = []

    for item in watchlist_items:
        symbol = item["symbol"]
        name = item.get("name", symbol)
        eval_rec: dict[str, Any] = {"symbol": symbol, "name": name}

        def _skip(reason: str) -> None:
            eval_rec["skip"] = reason
            evaluated.append(eval_rec)

        try:
            snapshot = intraday.fetch_intraday_snapshot(symbol, name=name, period="30", bar_limit=8)
        except Exception as e:  # noqa: BLE001
            logger.debug("[TAIL-HUNT] 获取 %s 盘中快照失败: %s", symbol, e)
            _skip(f"盘中快照拉取失败: {e}")
            continue

        # 数据防护：拒绝 mock、无数据或非正价格
        if getattr(snapshot, "is_mock", False) or not snapshot.bars or (snapshot.current_price or 0) <= 0:
            _skip("mock/无数据/价格异常")
            continue

        current_price = snapshot.current_price
        bars = snapshot.bars
        if len(bars) < 2:
            _skip("K线数量不足")
            continue

        # 仅取当日 K 线（30分钟单日最多 8 根），避免跨日聚合失真
        latest_date = bars[-1].timestamp.date()
        today_bars = [b for b in bars if b.timestamp.date() == latest_date]
        if not today_bars:
            _skip("无当日K线")
            continue

        high = max(b.high for b in today_bars)
        low = min(b.low for b in today_bars)
        day_range = high - low
        close_pos = (current_price - low) / day_range if day_range > 0 else 0.5
        amplitude = day_range / snapshot.prev_close if snapshot.prev_close > 0 else 0.0
        turnover = sum(b.turnover for b in today_bars) if any(b.turnover > 0 for b in today_bars) else snapshot.turnover
        change_pct = snapshot.change_pct
        eval_rec.update({
            "price": current_price,
            "change_pct": round(change_pct, 2),
            "close_pos": round(close_pos, 2),
            "amplitude": round(amplitude, 4),
            "turnover": turnover,
        })

        # 流动性与振幅硬过滤
        if turnover < _MIN_TURNOVER or amplitude < _MIN_AMPLITUDE:
            _skip(f"流动性/振幅不足 (turnover={turnover/1e8:.2f}亿, amplitude={amplitude:.1%})")
            continue

        # 匹配形态 A (强势延续) 或 形态 B (强势企稳)
        matched_setup = None

        # 形态 A：收红 + 位于振幅前 35% 高位区
        if change_pct >= 0.0 and close_pos >= _CONT_CLOSE_POS:
            matched_setup = "momentum_continuation"
        # 形态 B：回踩企稳且跌幅不深（跌幅 > -7% 且收出下影线）
        elif change_pct >= -7.0 and (current_price >= snapshot.open or (min(snapshot.open, current_price) - low) / max(day_range, 0.01) >= 0.4):
            matched_setup = "strong_pullback"

        if not matched_setup:
            _skip("未匹配尾盘形态 (非强势延续/强势企稳)")
            continue

        if matched_setup:
            score = (close_pos * 40.0) + (min(turnover / 1e8, 20.0) * 2.0) + (change_pct * 2.0)
            eval_rec.update({"setup": matched_setup, "score": round(score, 2)})
            evaluated.append(eval_rec)
            candidates_scored.append({
                "symbol": symbol,
                "name": name,
                "price": current_price,
                "turnover": turnover,
                "amplitude": amplitude,
                "change_pct": change_pct,
                "setup": matched_setup,
                "score": score,
                "watch_id": item["watch_id"],
            })

    if not candidates_scored:
        logger.info("[TAIL-HUNT] 自选股池中未发现满足 14:30 尾盘触发条件的标的")
        return _done({"executed": False, "reason": "无标的满足尾盘触发条件", "action": "SKIP"})

    # 5. 挑出得分最高的一只标的建仓
    candidates_scored.sort(key=lambda x: x["score"], reverse=True)
    best = candidates_scored[0]
    symbol = best["symbol"]
    price = best["price"]

    # 仓位计算：单笔最多 20% 资金，不超过可用资金
    total_capital = float(account.get("total_value", 300000.0)) if account else 300000.0
    target_amount = min(total_capital * 0.20, available_cash * 0.90)
    qty = max(int(target_amount / price // 100) * 100, 100)

    # 6. 构建买入信号并通过 ExecutionEngine 下单
    signal = {
        "signal_id": f"SIG-TAIL-{uuid.uuid4().hex[:8].upper()}",
        "symbol": symbol,
        "name": best["name"],
        "action": "buy",
        "entry_price": price,
        "current_price": price,
        "market_price_at_trigger": price,
        "target_price": round(price * 1.08, 2),
        "stop_loss": round(price * 0.975, 2),
        "position_pct": round(target_amount / total_capital, 2),
        "target_qty": qty,
        "strategy": STRATEGY_ID,
        "strategy_id": STRATEGY_ID,
        "entry_condition": "immediate",
        "rationale": f"14:35尾盘狙击触发: 形态={best['setup']}, 现价={price:.2f}, 振幅={best['amplitude']:.1%}, 成交额={best['turnover']/1e8:.2f}亿",
        "timestamp": datetime.now().isoformat(),
        "expiry": None,
    }

    engine = ExecutionEngine(session_id, persona_id=persona_id, brain=brain)
    orders, fills = await engine.monitor_and_execute([signal])

    if fills:
        # 标记 watchlist 为 triggered
        brain.store.trigger_watchlist_item(best["watch_id"])
        fill_price = fills[0]["avg_price"]
        fill_qty = fills[0]["quantity"]
        logger.info(
            "🎯 [TAIL-HUNT] 尾盘狙击成交成功! %s %s | 数量=%d | 成交价=%.2f | 止损=%.2f | 目标=%.2f",
            symbol, best["name"], fill_qty, fill_price, signal["stop_loss"], signal["target_price"],
        )
        return _done({
            "executed": True,
            "symbol": symbol,
            "name": best["name"],
            "fill_price": fill_price,
            "quantity": fill_qty,
            "setup": best["setup"],
            "orders": orders,
            "fills": fills,
        })

    logger.warning("[TAIL-HUNT] %s 下单未产生成交", symbol)
    return _done({"executed": False, "symbol": symbol, "reason": "下单未成交"})


async def execute_morning_pulse(
    brain=None,
    persona_id: str = PERSONA_ID,
    session_id: str | None = None,
) -> dict[str, Any]:
    """09:35 早盘脉冲：委托给统一 hard_scan 执行（消除双轨冲突与规则分叉）。"""
    from src.agents.reviewer.hard_scan import run_hard_scan

    brain = brain or get_central_brain()
    session_id = session_id or f"morning-pulse-{datetime.now().strftime('%Y%m%d-%H%M')}-{uuid.uuid4().hex[:4]}"
    logger.info("⏰ [MORNING-PULSE] 开始执行 09:35 早盘脉冲巡检 (persona=%s)", persona_id)

    def _emit(result: dict[str, Any]) -> dict[str, Any]:
        try:
            brain.log_agent_event(session_id, "tail_hunt", "morning_pulse_complete", result)
        except Exception:
            logger.debug("morning_pulse_complete 事件写入失败", exc_info=True)
        return result

    positions = brain.store.list_open_positions(persona_id=persona_id)
    if not positions:
        logger.info("[MORNING-PULSE] 当前无持仓，跳过")
        return _emit({"checked": 0, "actions": []})

    results = await run_hard_scan(persona_id=persona_id, force=True, brain=brain)
    actions = [r for r in results if r.get("action") in ("EXIT", "REDUCE")]
    return _emit({
        "checked": len(positions),
        "actions": [
            {
                "symbol": r.get("symbol"),
                "action": r.get("action"),
                "price": r.get("current_price"),
                "pnl_pct": r.get("pnl_pct"),
                "reason": r.get("reason", ""),
                "executed": r.get("executed"),
            }
            for r in actions
        ],
    })


async def execute_force_close_ultra_short(
    brain=None,
    persona_id: str = PERSONA_ID,
    session_id: str | None = None,
) -> dict[str, Any]:
    """14:30 次日强制清仓：持仓满 1 个交易日无论盈亏必须出局，兑现 T+1 纪律。"""
    brain = brain or get_central_brain()
    session_id = session_id or f"force-close-{datetime.now().strftime('%Y%m%d-%H%M')}-{uuid.uuid4().hex[:4]}"
    logger.info("⏰ [FORCE-CLOSE] 开始执行 14:30 隔夜持仓强制清仓检查 (persona=%s)", persona_id)

    def _emit(result: dict[str, Any]) -> dict[str, Any]:
        try:
            brain.log_agent_event(session_id, "tail_hunt", "force_close_complete", result)
        except Exception:
            logger.debug("force_close_complete 事件写入失败", exc_info=True)
        return result

    positions = brain.store.list_open_positions(persona_id=persona_id)
    if not positions:
        return _emit({"closed": 0})

    today_str = date.today().isoformat()
    intraday = IntradayClient(allow_mock_fallback=False)
    closed_count = 0
    closed_items: list[dict[str, Any]] = []

    for pos in positions:
        entry_date_str = pos.get("entry_date") or ""
        # 如果不是今天买入的（即昨天或更早买入），必须强制清仓
        if entry_date_str and entry_date_str < today_str:
            symbol = pos["symbol"]
            name = pos.get("name", symbol)

            # 必须拿到真实行情才能成交：禁止用成本价/假价格伪造平仓（会产生 0% 假盈亏）
            current_price = 0.0
            try:
                snapshot = intraday.fetch_intraday_snapshot(symbol, name=name, period="1", bar_limit=5)
                if snapshot.current_price and not getattr(snapshot, "is_mock", False):
                    current_price = float(snapshot.current_price)
            except Exception as e:  # noqa: BLE001
                logger.warning("[FORCE-CLOSE] %s 拉取行情异常: %s", symbol, e)
            if current_price <= 0:
                logger.error("🚫 [FORCE-CLOSE] %s %s 无真实行情，跳过强平（交由后续 hard_scan / 人工处理）", symbol, name)
                notify_ops(
                    "隔夜强平跳过：无真实行情",
                    f"persona={persona_id} symbol={symbol} entry_date={entry_date_str}",
                )
                closed_items.append({
                    "symbol": symbol,
                    "name": name,
                    "price": None,
                    "executed": False,
                    "error": "no_real_quote",
                })
                continue

            reason = f"持仓已达 1 个交易日 (入场日期: {entry_date_str})，触发隔夜短线强制清仓纪律"
            logger.info("🚨 [FORCE-CLOSE] 强制清仓: %s %s @ %.2f (%s)", symbol, name, current_price, reason)

            review = {
                "action": "EXIT",
                "current_price": current_price,
                "reason": reason,
            }
            res = await _execute_review_action(
                brain,
                session_id=session_id,
                position=pos,
                review=review,
                persona_id=persona_id,
            )
            if res.get("executed"):
                closed_count += 1
            closed_items.append({
                "symbol": symbol,
                "name": name,
                "price": current_price,
                "executed": bool(res.get("executed")),
                "error": res.get("execution_error"),
            })

    return _emit({"closed": closed_count, "items": closed_items})
