"""Stale Position Check — 长期持仓预警机制。

当持仓超过阈值天数未平仓时，发出 WARNING 日志并通过 EventBus 广播事件，
同时为盘中复审提供 force_review_reason 以促使更积极的卖出建议。
"""
from __future__ import annotations

import logging
from datetime import date

from src.central_brain import get_central_brain

logger = logging.getLogger(__name__)

DEFAULT_STALE_THRESHOLD_DAYS = 5

# 人格级别的 stale 阈值 — 价值投资允许更长持仓期
_PERSONA_STALE_THRESHOLDS: dict[str, int] = {
    "short_term_hot_rotation_v1": 5,    # 短线: 5 天
    "warren_buffett_v1": 90,            # 巴菲特: 90 天
    "duan_yongping_v1": 90,             # 段永平: 90 天
}


def _get_threshold_for_persona(persona_id: str | None, default: int) -> int:
    """根据人格 ID 返回 stale 阈值天数。"""
    if persona_id and persona_id in _PERSONA_STALE_THRESHOLDS:
        return _PERSONA_STALE_THRESHOLDS[persona_id]
    return default


def check_stale_positions(
    threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
    persona_id: str | None = None,
) -> list[dict]:
    """检查所有超期持仓并发出预警。

    Args:
        threshold_days: 持仓天数阈值（交易日近似为自然日），超过则标记为 stale。
                        如果指定了 persona_id，会自动使用人格对应阈值覆盖此值。
        persona_id: 可选人格ID过滤，为None时检查所有持仓。

    Returns:
        被标记为 stale 的持仓列表（包含 hold_days 和 force_review_reason）。
    """
    # 人格级别阈值覆盖
    threshold_days = _get_threshold_for_persona(persona_id, threshold_days)

    brain = get_central_brain()
    # 默认人格同时接管未指派持仓，避免旧数据 stale 检测漏掉。
    include_unassigned = persona_id == "short_term_hot_rotation_v1"
    positions = brain.store.list_open_positions(
        persona_id=persona_id,
        include_unassigned=include_unassigned,
    )

    if not positions:
        logger.info("无持仓，跳过 stale position 检查")
        return []

    today = date.today()
    stale_positions: list[dict] = []

    for pos in positions:
        entry_date_str = pos.get("entry_date", "")
        if not entry_date_str:
            continue

        try:
            entry_date = date.fromisoformat(entry_date_str)
        except (ValueError, TypeError):
            logger.debug("无法解析 entry_date: %s (position %s)", entry_date_str, pos.get("position_id"))
            continue

        hold_days = (today - entry_date).days
        if hold_days <= threshold_days:
            continue

        # 标记为 stale
        pnl_pct = _calc_unrealized_pnl_pct(pos)
        force_reason = (
            f"持仓已达{hold_days}天(阈值{threshold_days}天)，"
            f"浮动盈亏{pnl_pct:+.1f}%，需重新评估是否继续持有"
        )

        stale_info = {
            **pos,
            "hold_days": hold_days,
            "force_review_reason": force_reason,
            "unrealized_pnl_pct": pnl_pct,
        }
        stale_positions.append(stale_info)

        logger.warning(
            "[STALE] %s %s | 持仓 %d 天 | 入场价 %.2f | 浮动盈亏 %.1f%% | %s",
            pos.get("symbol", "?"),
            pos.get("name", ""),
            hold_days,
            pos.get("entry_price", 0),
            pnl_pct,
            force_reason,
        )

    # 汇总日志
    if stale_positions:
        logger.warning(
            "[STALE SUMMARY] %d/%d 只持仓超过 %d 天阈值",
            len(stale_positions), len(positions), threshold_days,
        )
        # 通过 EventBus 广播
        brain.log_agent_event(
            session_id=f"stale-check-{today.isoformat()}",
            agent="reviewer",
            event_type="stale_positions_detected",
            payload={
                "check_date": today.isoformat(),
                "threshold_days": threshold_days,
                "total_open": len(positions),
                "stale_count": len(stale_positions),
                "stale_symbols": [
                    {
                        "symbol": p["symbol"],
                        "name": p.get("name", ""),
                        "hold_days": p["hold_days"],
                        "pnl_pct": p["unrealized_pnl_pct"],
                    }
                    for p in stale_positions
                ],
            },
        )
    else:
        logger.info(
            "所有 %d 只持仓均在 %d 天阈值内，无需预警",
            len(positions), threshold_days,
        )

    return stale_positions


def _calc_unrealized_pnl_pct(position: dict) -> float:
    """估算未实现盈亏百分比（基于最近复审价或入场价）。"""
    entry_price = position.get("entry_price", 0)
    if entry_price <= 0:
        return 0.0

    # 尝试从最近复审获取当前价格
    brain = get_central_brain()
    reviews = brain.store.list_position_reviews(position["position_id"], limit=1)
    if reviews:
        current_price = reviews[0].get("current_price", entry_price)
    else:
        current_price = entry_price

    pnl_pct = (current_price / entry_price - 1) * 100
    if position.get("side") == "short":
        pnl_pct = -pnl_pct

    return round(pnl_pct, 2)
