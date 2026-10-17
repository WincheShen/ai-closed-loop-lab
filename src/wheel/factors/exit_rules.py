"""rule.* — 出场规则提议 Factor。

全部复用 src/agents/reviewer/hard_rules.py 的 check_* 函数，
与线上 evaluate_hard_exit / 回测引擎同源，优先级顺序一致。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.agents.reviewer import hard_rules
from src.wheel.protocol import Context, Opinion


def _position_and_price(ctx: Context) -> tuple[dict, float] | None:
    pos = ctx.data.get("position")
    quote = ctx.data.get("quote") or {}
    price = float(quote.get("price") or 0)
    if not pos or price <= 0:
        return None
    return pos, price


class _HardRuleFactor:
    """把 hard_rules.check_* 包装成 propose Factor。"""

    stage = "propose"
    requires = ("position", "quote")
    name: str = ""
    priority: int = 100
    check: Callable[..., dict[str, Any] | None] = staticmethod(lambda *a, **k: None)
    param_keys: tuple[str, ...] = ()

    def _params(self, params: dict) -> dict:
        return {k: params[k] for k in self.param_keys if k in params}

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        pair = _position_and_price(ctx)
        if not pair:
            return Opinion(factor=self.name, verdict="skip", reason="无持仓或无效行情")
        pos, price = pair
        kwargs = self._params(params)
        if "today" in self.param_keys and "today" not in kwargs:
            kwargs["today"] = ctx.now.date().isoformat()
        hit = self.check(pos, price, **kwargs)
        if not hit:
            return Opinion(factor=self.name, verdict="pass")
        return Opinion(
            factor=self.name,
            verdict="propose",
            action=hit["action"],
            priority=self.priority,
            reason=hit.get("reason", ""),
            evidence={
                "risk_flag": hit.get("risk_flag", ""),
                "confidence": hit.get("confidence", 1.0),
                "key_observation": hit.get("key_observation", ""),
                "price": price,
            },
        )


class StopLossRule(_HardRuleFactor):
    name = "rule.stop_loss"
    priority = 10
    check = staticmethod(hard_rules.check_stop_loss)


class MaxLossRule(_HardRuleFactor):
    name = "rule.max_loss"
    priority = 11
    check = staticmethod(hard_rules.check_max_loss)
    param_keys = ("max_loss_pct",)


class TargetReduceRule(_HardRuleFactor):
    name = "rule.target_reduce"
    priority = 20
    check = staticmethod(hard_rules.check_target_reduce)


class NarrowStopRule(_HardRuleFactor):
    name = "rule.narrow_stop"
    priority = 10
    check = staticmethod(hard_rules.check_narrow_stop)
    param_keys = ("stop_pct",)


class PulseTakeProfitRule(_HardRuleFactor):
    name = "rule.pulse_take_profit"
    priority = 20
    check = staticmethod(hard_rules.check_pulse_take_profit)
    param_keys = ("take_profit_pct",)


class OvernightExpiredRule(_HardRuleFactor):
    name = "rule.overnight_expired"
    priority = 10
    check = staticmethod(hard_rules.check_overnight_expired)
    param_keys = ("today",)


def register(reg) -> None:
    reg.register_factor(StopLossRule())
    reg.register_factor(MaxLossRule())
    reg.register_factor(TargetReduceRule())
    reg.register_factor(NarrowStopRule())
    reg.register_factor(PulseTakeProfitRule())
    reg.register_factor(OvernightExpiredRule())
