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


def _proposed_by(resolved) -> str:
    """裁决提议者：proposed_by 在 modify 阶段不被改写；缺省回退 decided_by。"""
    return getattr(resolved, "proposed_by", "") or resolved.decided_by or ""


class StopConfirmModifier:
    """止损二次确认 — 单 tick 击穿不立即卖，窗口内连续击穿才确认。

    "止损类条件"当前是否成立：现价 ≤ 止损价，或浮亏 ≤ -max_loss_pct（熔断线）。
    - 条件不成立（已收复）→ 清除登记；
    - 条件成立且裁决为 EXIT → 计入确认次数；含 LLM 提议恰逢止损位（堵绕过通道）；
    - 同一观测期内（min_breach_gap_seconds）的重复评估不计数 — 防止同一时刻
      hard_scan 与 LLM 复审各自跑一遍转盘、单 tick 就凑满两次确认。

    豁免：隔夜极速动量（窄止损即时生效）、灾难性击穿（立即出）。
    """

    name = "mod.stop_confirm"
    stage = "modify"
    requires = ("position", "quote")
    param_keys = (
        "required_breaches", "confirm_window_minutes",
        "min_breach_gap_seconds", "max_loss_pct",
    )

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        pair = _pos_price(ctx)
        resolved = ctx.resolved
        if not pair or not resolved:
            return Opinion(factor=self.name, verdict="skip", reason="无持仓/裁决")
        pos, price = pair
        if price <= 0:
            return Opinion(factor=self.name, verdict="pass", reason="无有效行情，不动状态")

        stop = float(pos.get("stop_loss") or 0)
        state = ctx.state.get(self.name) or {}
        pnl_pct = position_pnl_pct(pos, price)
        max_loss_pct = abs(float(params.get("max_loss_pct", 8.0)))

        # 止损条件本身判断"是否已收复"，与本次裁决动作无关
        # （-8% 熔断时价格常在止损线上方，不能用 price > stop 当收复 — 修复窗口绕过）
        breach_now = (stop > 0 and price <= stop) or pnl_pct <= -max_loss_pct

        if not breach_now:
            if state:
                return Opinion(
                    factor=self.name, verdict="pass",
                    reason="止损条件已解除，清除击穿登记", clear_state=True,
                )
            return Opinion(factor=self.name, verdict="pass")

        # 仍在击穿但裁决不是卖出（LLM HOLD 等）→ 保留登记、不修饰；
        # 隔夜客窄止损即时生效，不需要二次确认。
        if resolved.action != "EXIT" or is_ultra_short(pos):
            return Opinion(factor=self.name, verdict="pass")

        catastrophic = (
            (stop > 0 and price <= stop * (1 - CATASTROPHIC_BELOW_STOP))
            or pnl_pct <= CATASTROPHIC_PNL_PCT
        )
        if catastrophic:
            return Opinion(
                factor=self.name, verdict="pass",
                reason=f"灾难性击穿 (PnL={pnl_pct:.1f}%)，立即清仓无需二次确认",
            )

        required = int(params.get("required_breaches", 2))
        window = timedelta(minutes=float(params.get("confirm_window_minutes", 15)))
        min_gap = timedelta(seconds=float(params.get("min_breach_gap_seconds", 180)))

        count = int(state.get("breach_count", 0))
        prev_at = state.get("last_breach_at")
        if prev_at:
            try:
                gap = ctx.now - datetime.fromisoformat(prev_at)
            except ValueError:
                gap = None
            if gap is None or gap > window:
                # 时间戳损坏或距上次有效击穿超出确认窗口 → 重新计为首次
                count = 0
            elif gap < min_gap:
                # 同一观测期内的重复评估（如同一时刻 hard_scan + LLM 复审）
                # → 不计数，只刷新最近观测价，继续等待下一次有效观测
                return _modify(
                    self.name, "HOLD",
                    f"击穿观测间隔不足 {int(min_gap.total_seconds())}s，本次不计数",
                    state_update={"price": price},
                    evidence={"original_action": "EXIT", "breach_count": count},
                )

        if count + 1 >= required:
            return Opinion(
                factor=self.name, verdict="modify", action="EXIT",
                reason=f"止损连续击穿 {count + 1} 次，二次确认通过",
                clear_state=True,
            )

        return _modify(
            self.name, "HOLD",
            f"击穿未确认（第 {count + 1} 次观测，"
            f"{window.seconds // 60} 分钟内间隔 ≥{int(min_gap.total_seconds())}s 再观测一次才清仓）",
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
    param_keys: tuple = ()

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
    param_keys: tuple = ()

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        resolved = ctx.resolved
        pos = ctx.data.get("position")
        if not resolved or resolved.action not in ("EXIT", "REDUCE") or not pos:
            return Opinion(factor=self.name, verdict="pass")
        if not _proposed_by(resolved).startswith("llm.") or not is_value_position(pos):
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
    param_keys = ("min_hold_days",)

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        resolved = ctx.resolved
        pos = ctx.data.get("position")
        if not resolved or resolved.action != "REDUCE" or not pos:
            return Opinion(factor=self.name, verdict="pass")
        if not _proposed_by(resolved).startswith("llm."):
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
