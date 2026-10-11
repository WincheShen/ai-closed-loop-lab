"""无 LLM 的盘中硬扫描：止损 / pending 触发 / 过期。

空仓也必须跑 — 审计发现 run_intraday_review 在无持仓时会直接 return，漏扫条件单。
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime

from src.agents.reviewer import stop_confirm
from src.agents.reviewer.hard_rules import evaluate_hard_exit, position_pnl_pct
from src.central_brain import get_central_brain
from src.stock_analyzer.data_source.intraday_client import IntradayClient

logger = logging.getLogger(__name__)

PENDING_TTL_DAYS = 5
ACTIVE_TTL_DAYS = 2


async def run_hard_scan(
    persona_id: str | None = None,
    *,
    force: bool = False,
    brain=None,
) -> list[dict]:
    """每 5 分钟：硬止损、条件单、过期。零 LLM。"""
    from src.agents.reviewer.intraday_loop import (
        _check_pending_signals,
        _execute_review_action,
        is_trading_hours,
    )

    if not force and not is_trading_hours():
        logger.info("非交易时段，跳过 hard_scan")
        return []

    brain = brain or get_central_brain()
    session_id = (
        f"hard-{date.today().isoformat()}-"
        f"{datetime.now().strftime('%H%M')}-{persona_id or 'all'}"
    )
    include_unassigned = persona_id == "short_term_hot_rotation_v1"
    positions = brain.store.list_open_positions(
        persona_id=persona_id,
        include_unassigned=include_unassigned,
    )

    # 价格缓存：同一轮扫描同标的只拉一次
    price_cache: dict[str, tuple[float, bool]] = {}
    intraday = IntradayClient(allow_mock_fallback=False)
    exits: list[dict] = []

    for pos in positions:
        symbol = pos.get("symbol", "")

        # A 股 T+1 交易纪律：当日买入的持仓无法在当日卖出
        entry_date = pos.get("entry_date") or ""
        today_str = date.today().isoformat()
        if entry_date == today_str and not force:
            logger.info("[HARD] %s 为当日建仓 (entry_date=%s, T+1 锁定)，无法当日卖出，保留持仓", symbol, entry_date)
            continue

        price, is_mock = _price_for(symbol, pos, intraday, price_cache)
        if is_mock or price <= 0:
            logger.warning("[HARD] %s 无真实行情，跳过硬止损", symbol)
            continue
        decision = evaluate_hard_exit(pos, price)
        if not stop_confirm.is_stop_decision(decision):
            stop_confirm.clear_breach(pos["position_id"])
        if decision is None:
            continue
        pnl_pct = position_pnl_pct(pos, price)
        if stop_confirm.requires_confirmation(pos, decision, price) and not stop_confirm.register_breach(
            pos["position_id"],
        ):
            logger.info("[HARD] %s 首次击穿止损 @ %.2f (PnL=%.2f%%)，等待下一轮扫描确认", symbol, price, pnl_pct)
            exits.append({
                "symbol": symbol,
                "position_id": pos["position_id"],
                "action": "HOLD",
                "current_price": price,
                "pnl_pct": round(pnl_pct, 2),
                "reason": f"首次击穿止损，等待二次确认（原判定: {decision.get('reason', '')}）",
                "risk_flag": "stop_breach_pending",
                "executed": False,
            })
            continue
        review = {
            **decision,
            "symbol": symbol,
            "position_id": pos["position_id"],
            "current_price": price,
            "pnl_pct": round(pnl_pct, 2),
            "review_id": f"REV-HARD-{uuid.uuid4().hex[:8].upper()}",
        }
        try:
            executed = await _execute_review_action(
                brain, session_id, pos, review, persona_id=persona_id,
            )
            review.update(executed)

            # 隔夜动量持仓平仓后，若趋势健康 (pnl_pct >= -3.5%)，自动回流自选池跟踪等待二次企稳
            is_ultra_short = (
                persona_id == "ultra_short_momentum_v1"
                or pos.get("persona_id") == "ultra_short_momentum_v1"
            )
            if (
                is_ultra_short
                and review.get("action") == "EXIT"
                and review.get("executed")
                and pnl_pct >= -3.5
            ):
                from src.agents.explorer.short_term_watchlist import ShortTermWatchlistManager
                wl_mgr = ShortTermWatchlistManager(brain)
                wl_mgr.add_rebound_watch(
                    symbol=symbol,
                    name=pos.get("name", symbol),
                    sector=pos.get("sector", ""),
                    last_price=price,
                    reason=f"隔夜持仓平仓(PnL={pnl_pct:+.1f}%)，自动回流自选池跟踪二次机会",
                )
        except Exception as exc:
            logger.error("[HARD] 执行 %s %s 失败: %s", review["action"], symbol, exc)
            review["execution_error"] = str(exc)
        exits.append(review)
        logger.info(
            "[HARD] %s %s @ %.2f — %s",
            symbol, review["action"], price, review.get("reason", ""),
        )

    triggered = await _check_pending_signals(brain, session_id, force=force)
    expired_n = expire_stale_signals(brain)

    if hasattr(brain, "log_agent_event"):
        brain.log_agent_event(
            session_id=session_id,
            agent="hard_scan",
            event_type="hard_scan_complete",
            payload={
                "persona_id": persona_id,
                "open_positions": len(positions),
                "hard_exits": len(exits),
                "pending_triggered": len(triggered),
                "expired": expired_n,
                "details": [
                    {
                        "symbol": r.get("symbol"),
                        "action": r.get("action"),
                        "price": r.get("current_price") or r.get("price"),
                        "pnl_pct": r.get("pnl_pct"),
                        "reason": r.get("reason", ""),
                        "executed": r.get("executed"),
                    }
                    for r in exits + triggered
                ],
            },
        )
    logger.info(
        "hard_scan 完成: 持仓=%d 硬动作=%d 条件单=%d 过期=%d",
        len(positions), len(exits), len(triggered), expired_n,
    )
    return exits + triggered


def expire_stale_signals(brain) -> int:
    """pending >5 天、active >2 天未成交 → expired。"""
    conn = brain.store._conn()
    pending = conn.execute(
        """SELECT signal_id, symbol FROM trade_signals
        WHERE status = 'pending'
          AND timestamp IS NOT NULL
          AND datetime(timestamp, ?) < datetime('now')""",
        (f"+{PENDING_TTL_DAYS} days",),
    ).fetchall()
    active = conn.execute(
        """SELECT signal_id, symbol FROM trade_signals
        WHERE status = 'active'
          AND timestamp IS NOT NULL
          AND datetime(timestamp, ?) < datetime('now')""",
        (f"+{ACTIVE_TTL_DAYS} days",),
    ).fetchall()
    n = 0
    for row in pending:
        brain.store.update_signal_status(row["signal_id"], "expired")
        logger.info("[EXPIRED] %s pending 超过 %d 天", row["symbol"], PENDING_TTL_DAYS)
        n += 1
    for row in active:
        brain.store.update_signal_status(row["signal_id"], "expired")
        logger.info("[EXPIRED] %s active 超过 %d 天未成交", row["symbol"], ACTIVE_TTL_DAYS)
        n += 1
    return n


def _price_for(
    symbol: str,
    position: dict,
    intraday: IntradayClient,
    cache: dict[str, tuple[float, bool]],
) -> tuple[float, bool]:
    if symbol in cache:
        return cache[symbol]
    try:
        snap = intraday.fetch_intraday_snapshot(
            symbol, name=position.get("name", ""), period="1", bar_limit=1,
        )
        price = float(snap.current_price or 0)
        is_mock = bool(getattr(snap, "is_mock", False))
    except Exception as exc:
        logger.warning("获取 %s 实时价格失败: %s", symbol, exc)
        price, is_mock = 0.0, True
    cache[symbol] = (price, is_mock)
    return price, is_mock
