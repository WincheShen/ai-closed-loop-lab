"""确定性持仓硬规则 — 不经过 LLM。

止损 / -8% 熔断（仅短线）/ 目标价减仓必须和盘中复审、回测引擎共用这一份。
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


def is_value_position(position: dict) -> bool:
    """价值投资持仓：长周期持有，不适用短线的 -8% 熔断与盘中噪音离场。"""
    persona_id = position.get("persona_id") or ""
    strategy = f"{position.get('strategy_id') or ''} {position.get('original_strategy') or ''}"
    return (
        any(k in persona_id for k in _VALUE_PERSONA_KEYS)
        or any(k in strategy for k in _VALUE_STRATEGY_KEYS)
    )


def evaluate_hard_exit(position: dict, current_price: float) -> dict[str, Any] | None:
    """价格触发则返回决策 dict，否则 None。

    优先级：止损 / 浮亏-8% > 目标价减仓。
    针对 ultra_short_momentum_v1 (隔夜极速动量)：
    - 极窄止损线：当前价 <= stop_loss 或 pnl_pct <= -2.5%
    - 冲高止盈线：当前价 >= target_price 或 pnl_pct >= 3.5% (全仓 EXIT，不减仓)
    """
    if current_price <= 0:
        return None
    pnl_pct = position_pnl_pct(position, current_price)
    stop_loss = float(position.get("stop_loss") or 0)
    target_price = float(position.get("target_price") or 0)
    persona_id = position.get("persona_id") or ""
    is_ultra_short = (
        persona_id == "ultra_short_momentum_v1"
        or position.get("strategy_id") == "ultra_short_momentum"
    )

    if is_ultra_short:
        # 1. 隔夜动量极窄止损 (触及 stop_loss 或 浮亏 <= -2.5%)
        if (stop_loss > 0 and current_price <= stop_loss) or pnl_pct <= -2.5:
            return {
                "action": "EXIT",
                "confidence": 1.0,
                "reason": (
                    f"隔夜极速动量触及极窄止损 (现价={current_price:.2f}, "
                    f"PnL={pnl_pct:.2f}%)，规则引擎强制清仓"
                ),
                "thesis_status": "invalidated",
                "key_observation": f"极窄止损: {pnl_pct:.2f}% <= -2.5%",
                "risk_flag": "stop_loss_triggered",
                "rule_override": True,
            }
        # 2. 隔夜动量冲高脉冲止盈 (触及 target_price 或 浮盈 >= +3.5%)
        if (target_price > 0 and current_price >= target_price) or pnl_pct >= 3.5:
            return {
                "action": "EXIT",
                "confidence": 1.0,
                "reason": (
                    f"隔夜极速动量触发冲高脉冲止盈 (现价={current_price:.2f}, "
                    f"PnL={pnl_pct:.2f}%)，规则引擎兑现利润"
                ),
                "thesis_status": "intact",
                "key_observation": f"脉冲止盈: {pnl_pct:.2f}% >= +3.5%",
                "risk_flag": "",
                "rule_override": True,
            }
        return None

    # --- 常规人格持仓硬规则 ---
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

    # -8% 熔断只适用于短线；价值持仓由其宽止损 (stop_loss) 约束，
    # 否则正常的 8% 波动就会把月级持仓打掉（历史 4 笔价值股全部死在 -8.0x%）
    if pnl_pct <= -8.0 and not is_value_position(position):
        return {
            "action": "EXIT",
            "confidence": 1.0,
            "reason": f"浮亏{pnl_pct:.1f}%超过-8%硬止损线，规则引擎强制清仓",
            "thesis_status": "invalidated",
            "key_observation": f"浮亏过大: {pnl_pct:.1f}%",
            "risk_flag": "max_loss_exceeded",
            "rule_override": True,
        }

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
