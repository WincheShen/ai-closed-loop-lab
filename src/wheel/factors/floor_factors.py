"""floor.* — 系统底线 veto Factor（不可配置、不可绕过）。"""
from __future__ import annotations

from src.wheel import floor
from src.wheel.protocol import Context, Opinion


class _FloorFactor:
    stage = "veto"
    requires: tuple[str, ...] = ()
    param_keys: tuple = ()

    def _veto(self, reason: str) -> Opinion:
        return Opinion(factor=self.name, verdict="veto", reason=reason)


class TradingDayFloor(_FloorFactor):
    name = "floor.trading_day"

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        reason = floor.check_trading_day(ctx.now)
        if reason:
            return self._veto(reason)
        return Opinion(factor=self.name, verdict="pass")


class TPlusOneFloor(_FloorFactor):
    name = "floor.t_plus_1"
    requires = ("position",)

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        reason = floor.check_t1(ctx.data["position"], ctx.now)
        if reason:
            return self._veto(reason)
        return Opinion(factor=self.name, verdict="pass")


class RealQuoteFloor(_FloorFactor):
    name = "floor.real_quote_required"

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        reason = floor.check_real_quote(ctx.data.get("quote"))
        if reason:
            return self._veto(reason)
        return Opinion(factor=self.name, verdict="pass")


def register(reg) -> None:
    reg.register_factor(TradingDayFloor())
    reg.register_factor(TPlusOneFloor())
    reg.register_factor(RealQuoteFloor())
