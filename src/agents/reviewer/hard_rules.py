"""确定性持仓硬规则 — 不经过 LLM。

止损 / -8% 熔断（仅短线）/ 目标价减仓必须和盘中复审、回测引擎共用这一份。

拆分为独立的 check_* 函数后，`evaluate_hard_exit` 只做优先级组合；
`src/wheel/factors/exit_rules.py` 的 rule.* Factor 复用同一批函数，
保证线上、转盘影子模式、回测三处语义一致。
"""
from __future__ import annotations

from typing import Any


def position_pnl_pct(position: dict, current_price: float) -> float:
    entry = float(position.get("entry_price") or 0)
    if entry <= 0 or current_price <= 0:
        return 0.0
    pnl = (current_price / entry - 1.0) * 100.0
    if position.get("side") == "short":
        pnl = -pnl
    return round(pnl, 6)


_VALUE_PERSONA_KEYS = ("duan", "buffett", "value")
_VALUE_STRATEGY_KEYS = ("value_investing", "价值投资", "护城河", "ROE", "分红")
_ULTRA_PERSONA_ID = "ultra_short_momentum_v1"
_ULTRA_STRATEGY_ID = "ultra_short_momentum"


def is_value_position(position: dict) -> bool:
    """价值投资持仓：长周期持有，不适用短线的 -8% 熔断与盘中噪音离场。"""
    persona_id = position.get("persona_id") or ""
    strategy = f"{position.get('strategy_id') or ''} {position.get('original_strategy') or ''}"
    return (
        any(k in persona_id for k in _VALUE_PERSONA_KEYS)
        or any(k in strategy for k in _VALUE_STRATEGY_KEYS)
    )


def is_ultra_short(position: dict) -> bool:
    """隔夜极速动量持仓：窄止损 + 脉冲止盈，止损即时生效不二次确认。"""
    return (
        position.get("persona_id") == _ULTRA_PERSONA_ID
        or position.get("strategy_id") == _ULTRA_STRATEGY_ID
    )


def check_narrow_stop(position: dict, current_price: float, *, stop_pct: float = 2.5) -> dict[str, Any] | None:
    """隔夜动量极窄止损：当前价 <= stop_loss 或 pnl_pct <= -stop_pct。"""
    pnl_pct = position_pnl_pct(position, current_price)
    stop_loss = float(position.get("stop_loss") or 0)
    if (stop_loss > 0 and current_price <= stop_loss) or pnl_pct <= -abs(stop_pct):
        return {
            "action": "EXIT",
            "confidence": 1.0,
            "reason": (
                f"隔夜极速动量触及极窄止损 (现价={current_price:.2f}, "
                f"PnL={pnl_pct:.2f}%)，规则引擎强制清仓"
            ),
            "thesis_status": "invalidated",
            "key_observation": f"极窄止损: {pnl_pct:.2f}% <= -{abs(stop_pct)}%",
            "risk_flag": "stop_loss_triggered",
            "rule_override": True,
        }
    return None


def check_pulse_take_profit(position: dict, current_price: float, *, take_profit_pct: float = 3.5) -> dict[str, Any] | None:
    """隔夜动量冲高脉冲止盈：当前价 >= target_price 或 pnl_pct >= +take_profit_pct。"""
    pnl_pct = position_pnl_pct(position, current_price)
    target_price = float(position.get("target_price") or 0)
    if (target_price > 0 and current_price >= target_price) or pnl_pct >= take_profit_pct:
        return {
            "action": "EXIT",
            "confidence": 1.0,
            "reason": (
                f"隔夜极速动量触发冲高脉冲止盈 (现价={current_price:.2f}, "
                f"PnL={pnl_pct:.2f}%)，规则引擎兑现利润"
            ),
            "thesis_status": "intact",
            "key_observation": f"脉冲止盈: {pnl_pct:.2f}% >= +{take_profit_pct}%",
            "risk_flag": "",
            "rule_override": True,
        }
    return None


def check_stop_loss(position: dict, current_price: float) -> dict[str, Any] | None:
    """常规止损：当前价 <= stop_loss。"""
    stop_loss = float(position.get("stop_loss") or 0)
    if stop_loss > 0 and current_price <= stop_loss:
        return {
            "action": "EXIT",
            "confidence": 1.0,
            "reason": (
                f"价格{current_price:.2f}已跌破止损价{stop_loss:.2f}，规则引擎强制清仓"
            ),
            "thesis_status": "invalidated",
            "key_observation": f"跌破止损: {current_price:.2f} <= {stop_loss:.2f}",
            "risk_flag": "stop_loss_triggered",
            "rule_override": True,
        }
    return None


def check_max_loss(position: dict, current_price: float, *, max_loss_pct: float = 8.0) -> dict[str, Any] | None:
    """浮亏熔断：pnl_pct <= -max_loss_pct。价值持仓由宽止损约束，不适用。"""
    pnl_pct = position_pnl_pct(position, current_price)
    if pnl_pct <= -abs(max_loss_pct) and not is_value_position(position):
        return {
            "action": "EXIT",
            "confidence": 1.0,
            "reason": f"浮亏{pnl_pct:.1f}%超过-{abs(max_loss_pct):.0f}%硬止损线，规则引擎强制清仓",
            "thesis_status": "invalidated",
            "key_observation": f"浮亏过大: {pnl_pct:.1f}%",
            "risk_flag": "max_loss_exceeded",
            "rule_override": True,
        }
    return None


def check_target_reduce(position: dict, current_price: float) -> dict[str, Any] | None:
    """目标价减仓：当前价 >= target_price → REDUCE。"""
    target_price = float(position.get("target_price") or 0)
    if target_price > 0 and current_price >= target_price:
        return {
            "action": "REDUCE",
            "confidence": 0.9,
            "reason": (
                f"价格{current_price:.2f}已达目标价{target_price:.2f}，规则引擎减仓锁利"
            ),
            "thesis_status": "intact",
            "key_observation": f"达到目标: {current_price:.2f} >= {target_price:.2f}",
            "risk_flag": "",
            "rule_override": True,
        }
    return None


def check_overnight_expired(position: dict, current_price: float, *, today: str) -> dict[str, Any] | None:
    """隔夜持仓到期强平：entry_date < today → EXIT（14:30 转盘用）。"""
    entry_date = position.get("entry_date") or ""
    if entry_date and entry_date < today:
        return {
            "action": "EXIT",
            "confidence": 1.0,
            "reason": (
                f"持仓已达 1 个交易日 (入场日期: {entry_date})，"
                "触发隔夜短线强制清仓纪律"
            ),
            "thesis_status": "intact",
            "key_observation": f"隔夜到期: entry_date={entry_date}",
            "risk_flag": "overnight_expired",
            "rule_override": True,
        }
    return None


def evaluate_hard_exit(position: dict, current_price: float) -> dict[str, Any] | None:
    """价格触发则返回决策 dict，否则 None。

    优先级：止损 / 浮亏-8% > 目标价减仓。
    针对 ultra_short_momentum_v1 (隔夜极速动量)：
    - 极窄止损线：当前价 <= stop_loss 或 pnl_pct <= -2.5%
    - 冲高止盈线：当前价 >= target_price 或 pnl_pct >= 3.5% (全仓 EXIT，不减仓)
    """
    if current_price <= 0:
        return None

    if is_ultra_short(position):
        return check_narrow_stop(position, current_price) or check_pulse_take_profit(
            position, current_price,
        )

    return (
        check_stop_loss(position, current_price)
        or check_max_loss(position, current_price)
        or check_target_reduce(position, current_price)
    )
