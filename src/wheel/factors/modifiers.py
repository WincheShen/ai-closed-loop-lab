"""mod.* — 修饰 Factor：只能把已裁决的动作调"弱"，不能调强。

- mod.stop_confirm：止损二次确认（状态落 wheel_factor_state，重启不丢）
- mod.reduce_daily_cap：REDUCE 每持仓每交易日最多 1 次
- mod.value_noise_filter：价值持仓忽略 LLM 盘中噪音卖出
- mod.min_hold：最低持仓期保护（拦截过早的 LLM REDUCE）
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from src.agents.reviewer.hard_rules import (
    is_ultra_short,
    is_value_position,
    position_pnl_pct,
)
from src.agents.reviewer.stop_confirm import (
    CATASTROPHIC_BELOW_STOP,
    CATASTROPHIC_PNL_PCT,
    STOP_FLAGS,
)
from src.wheel.protocol import Context, Opinion

logger = logging.getLogger(__name__)


def _pos_price(ctx: Context) -> tuple[dict, float] | None:
    pos = ctx.data.get("position")
    quote = ctx.data.get("quote") or {}
    price = float(quote.get("price") or 0)
    if not pos:
        return None
    return pos, price


def _modify(name: str, action: str, reason: str, **kw) -> Opinion:
    return Opinion(factor=name, verdict="modify", action=action, reason=reason, **kw)


class StopConfirmModifier:
    """止损二次确认 — 单 tick 击穿不立即卖，窗口内连续击穿才确认。

    判定为"止损类退出"的条件（任一）：
    - 裁决风险标记属于 STOP_FLAGS（规则提议）
    - 当前价已跌破持仓止损价（含 LLM 提议恰逢止损位 — 堵住绕过确认的通道）

    豁免：隔夜极速动量（窄止损即时生效）、灾难性击穿（立即出）。
    """

    name = "mod.stop_confirm"
    stage = "modify"
    requires = ("position", "quote")

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        pair = _pos_price(ctx)
        resolved = ctx.resolved
        if not pair or not resolved:
            return Opinion(factor=self.name, verdict="skip", reason="无持仓/裁决")
        pos, price = pair
        stop = float(pos.get("stop_loss") or 0)
        state = ctx.state.get(self.name) or {}

        # 价格收复止损上方 → 清除登记，不做任何修饰
        if stop > 0 and price > stop and state:
            return Opinion(
                factor=self.name, verdict="pass",
                reason="价格收复止损上方，清除击穿登记", clear_state=True,
            )

        if resolved.action != "EXIT" or price <= 0:
            return Opinion(factor=self.name, verdict="pass")

        is_stop_exit = (
            resolved.risk_flag in STOP_FLAGS
            or (stop > 0 and price <= stop)
        )
        if not is_stop_exit or is_ultra_short(pos):
            return Opinion(factor=self.name, verdict="pass")

        pnl_pct = position_pnl_pct(pos, price)
        catastrophic = (
            (stop > 0 and price <= stop * (1 - CATASTROPHIC_BELOW_STOP))
            or pnl_pct <= CATASTROPHIC_PNL_PCT
        )
        if catastrophic:
            return Opinion(
                factor=self.name, verdict="pass",
                reason=f"灾难性击穿 (PnL={pnl_pct:.1f}%)，立即清仓无需二次确认",
            )

        window = timedelta(minutes=float(params.get("confirm_window_minutes", 15)))
        required = int(params.get("required_breaches", 2))
        prev_at = state.get("last_breach_at")
        count = int(state.get("breach_count", 0))
        if prev_at:
            try:
                within = ctx.now - datetime.fromisoformat(prev_at) <= window
            except ValueError:
                within = False
            if not within:
                count = 0  # 窗口外重新计为首次

        if count + 1 >= required:
            return Opinion(
                factor=self.name, verdict="modify", action="EXIT",
                reason=f"止损连续击穿 {count + 1} 次，二次确认通过",
                clear_state=True,
            )

        return _modify(
            self.name, "HOLD",
            f"首次击穿止损 (现价 {price:.2f} ≤ 止损 {stop:.2f})，"
            f"{window.seconds // 60} 分钟内再次击穿才确认",
            state_update={
                "last_breach_at": ctx.now.isoformat(),
                "breach_count": count + 1,
                "price": price,
            },
            evidence={"original_action": "EXIT", "breach_count": count + 1},
        )


class ReduceDailyCapModifier:
    """REDUCE 每持仓每交易日最多 1 次（防"切香肠"式减仓）。"""

    name = "mod.reduce_daily_cap"
    stage = "modify"
    requires = ("position",)

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        resolved = ctx.resolved
        pos = ctx.data.get("position")
        if not resolved or resolved.action != "REDUCE" or not pos:
            return Opinion(factor=self.name, verdict="pass")
        persona_id = pos.get("persona_id") or ctx.persona.persona_id
        if ctx.brain.store.sold_today(persona_id, pos.get("symbol", ""), today=ctx.now.date()):
            return _modify(
                self.name, "HOLD",
                "今日已减仓过，REDUCE 每日上限 1 次",
            )
        return Opinion(factor=self.name, verdict="pass")


class ValueNoiseFilterModifier:
    """价值持仓忽略 LLM 盘中噪音卖出；只有论点失效 (invalidated) 才放行。"""

    name = "mod.value_noise_filter"
    stage = "modify"
    requires = ("position",)

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        resolved = ctx.resolved
        pos = ctx.data.get("position")
        if not resolved or resolved.action not in ("EXIT", "REDUCE") or not pos:
            return Opinion(factor=self.name, verdict="pass")
        if not resolved.decided_by.startswith("llm.") or not is_value_position(pos):
            return Opinion(factor=self.name, verdict="pass")
        if resolved.evidence.get("thesis_status", "intact") == "invalidated":
            return Opinion(factor=self.name, verdict="pass")
        return _modify(
            self.name, "HOLD",
            f"价值持仓不因盘中波动离场（LLM原判定 {resolved.action}，论点未失效）",
        )


class MinHoldModifier:
    """最低持仓期保护：短线 <2 天、价值 <5 天时拦截 LLM REDUCE。"""

    name = "mod.min_hold"
    stage = "modify"
    requires = ("position",)

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        resolved = ctx.resolved
        pos = ctx.data.get("position")
        if not resolved or resolved.action != "REDUCE" or not pos:
            return Opinion(factor=self.name, verdict="pass")
        if not resolved.decided_by.startswith("llm."):
            return Opinion(factor=self.name, verdict="pass")
        entry_date = pos.get("entry_date") or ""
        try:
            days_held = (ctx.now.date() - ctx.now.date().fromisoformat(entry_date)).days
        except (ValueError, TypeError):
            return Opinion(factor=self.name, verdict="pass")
        min_hold = int(params.get("min_hold_days", 5 if is_value_position(pos) else 2))
        if days_held < min_hold:
            return _modify(
                self.name, "HOLD",
                f"持仓 {days_held} 天未达最低持仓期 {min_hold} 天，LLM 减仓暂缓",
            )
        return Opinion(factor=self.name, verdict="pass")


def register(reg) -> None:
    reg.register_factor(StopConfirmModifier())
    reg.register_factor(ReduceDailyCapModifier())
    reg.register_factor(ValueNoiseFilterModifier())
    reg.register_factor(MinHoldModifier())
