"""止损二次确认 — 防止盘中单根针（单 tick）击穿止损就清仓。

规则（仅常规人格的止损类退出；隔夜极速动量的窄止损保持即时，与回测语义一致）：
- 首次击穿只登记，不卖出；CONFIRM_WINDOW 内下一次扫描仍击穿 → 确认清仓
- 中途价格收复 → 清除登记
- 灾难性击穿（止损价触发时低于止损价 3%；-8% 熔断触发时浮亏 ≤ -12%）→ 立即清仓

状态保存在调度进程内存（hard_scan 与盘中复审同进程）。进程重启只会让确认多等一轮，
方向是保守的，不会误卖。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from src.agents.reviewer.hard_rules import position_pnl_pct

logger = logging.getLogger(__name__)

STOP_FLAGS = frozenset({"stop_loss_triggered", "max_loss_exceeded"})
CONFIRM_WINDOW = timedelta(minutes=15)
CATASTROPHIC_BELOW_STOP = 0.03
CATASTROPHIC_PNL_PCT = -12.0

_breaches: dict[str, datetime] = {}


def _is_ultra_short(position: dict) -> bool:
    return (
        position.get("persona_id") == "ultra_short_momentum_v1"
        or position.get("strategy_id") == "ultra_short_momentum"
    )


def is_stop_decision(decision: dict | None) -> bool:
    return bool(
        decision
        and decision.get("action") == "EXIT"
        and decision.get("risk_flag") in STOP_FLAGS
    )


def is_catastrophic(position: dict, decision: dict, current_price: float) -> bool:
    """止损价触发：低于止损价 3% 以上；-8% 熔断触发：浮亏 ≤ -12%。"""
    if decision.get("risk_flag") == "stop_loss_triggered":
        stop = float(position.get("stop_loss") or 0)
        return stop > 0 and current_price <= stop * (1 - CATASTROPHIC_BELOW_STOP)
    return position_pnl_pct(position, current_price) <= CATASTROPHIC_PNL_PCT


def requires_confirmation(position: dict, decision: dict | None, current_price: float) -> bool:
    return (
        is_stop_decision(decision)
        and not _is_ultra_short(position)
        and not is_catastrophic(position, decision, current_price)
    )


def register_breach(position_id: str, now: datetime | None = None) -> bool:
    """登记一次击穿；若窗口内已有前一次击穿则返回 True（已确认）。"""
    now = now or datetime.now()
    prev = _breaches.get(position_id)
    if prev is not None and now - prev <= CONFIRM_WINDOW and now > prev:
        _breaches.pop(position_id, None)
        return True
    _breaches[position_id] = now
    return False


def clear_breach(position_id: str) -> None:
    _breaches.pop(position_id, None)


def reset() -> None:
    _breaches.clear()
