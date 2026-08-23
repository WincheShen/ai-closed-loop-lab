"""确定性持仓硬规则 — 不经过 LLM。

止损 / -8% 熔断 / 目标价减仓必须和盘中复审、回测引擎共用这一份。
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


def evaluate_hard_exit(position: dict, current_price: float) -> dict[str, Any] | None:
    """价格触发则返回决策 dict，否则 None。

    优先级：止损 / 浮亏-8% > 目标价减仓。
    """
    if current_price <= 0:
        return None
    pnl_pct = position_pnl_pct(position, current_price)
    stop_loss = float(position.get("stop_loss") or 0)
    target_price = float(position.get("target_price") or 0)

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

    if pnl_pct <= -8.0:
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
