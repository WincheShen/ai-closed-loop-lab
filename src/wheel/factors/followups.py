"""后续观察与紧急复审触发 Factor（Phase 2）。

- mod.watch_followup：不改动作，只登记"1 分钟后再看"的后续事件。
  事件真正落库与时段/次数/作废约束由 Dispatcher 统一执行（§3.3）。
- rule.urgent_review：市场事件（market.regime_drift）触发持仓复审提议，
  动作 "REVIEW" 不是卖出，ExitGateway 不执行 — shadow 阶段只记录
  "该持仓会被复审"，修复 emergency_review_requested 只写不读的断链。
"""
from __future__ import annotations

from datetime import time, timedelta
from typing import Any

from src.wheel.protocol import Context, FollowUp, Opinion


class WatchFollowupModifier:
    """mod.watch_followup — 持仓未平则在截止时间前登记下一次观察。

    放在 modify 段尾部：能读到 ctx.resolved，持仓已 EXIT 就不再登记。
    返回 verdict=pass，不修改动作（modify 只许调弱，它本来就不动）。
    observer=True：行情暂时不可用被底线否决时仍会运行（见 runner）。
    """

    name = "mod.watch_followup"
    stage = "modify"
    observer = True
    requires = ("position",)
    param_keys = ("interval_seconds", "max_attempts", "until", "event_type")

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        resolved = ctx.resolved
        if resolved is None:
            return Opinion(factor=self.name, verdict="skip", reason="无裁决")
        if resolved.action == "EXIT":
            return Opinion(factor=self.name, verdict="pass", reason="持仓已平，不再观察")

        until = _parse_until(params.get("until", "14:30"))
        if until and ctx.now.time() >= until:
            return Opinion(
                factor=self.name, verdict="pass",
                reason=f"已过观察截止 {until.strftime('%H:%M')}，不再登记后续",
            )

        interval = float(params.get("interval_seconds", 60))
        fu = FollowUp(
            type=str(params.get("event_type", "followup.morning_watch")),
            after=timedelta(seconds=interval),
            subject=dict(ctx.event.subject or {}),
            payload={
                "ref_price": float(resolved.price or 0),
                "wheel": resolved.wheel,
                "persona_id": ctx.persona.persona_id,
            },
            max_attempts=int(params.get("max_attempts", 30)),
            until=until,
        )
        return Opinion(
            factor=self.name, verdict="pass",
            reason=f"登记后续观察（{int(interval)}s 后，最多 {fu.max_attempts} 次）",
            follow_up=fu,
        )


class UrgentReviewFactor:
    """rule.urgent_review — 持仓存在即提议 REVIEW（市场事件驱动的紧急复审）。

    action "REVIEW" 不是卖出动作，网关不执行；Phase 2 影子期它只把
    "该持仓应被紧急复审"落成可核对的决策记录。live 后由 Dispatcher
    把 REVIEW 映射到实际复审入口。
    """

    name = "rule.urgent_review"
    stage = "propose"
    requires = ("position",)
    priority = 10
    param_keys: tuple = ()

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        pos = ctx.data.get("position") or {}
        if not pos or pos.get("status") != "open":
            return Opinion(factor=self.name, verdict="pass", reason="无在持持仓")
        return Opinion(
            factor=self.name,
            verdict="propose",
            action="REVIEW",
            priority=self.priority,
            reason=f"市场事件 {ctx.event.type} 触发持仓紧急复审",
            evidence={
                "symbol": pos.get("symbol", ""),
                "parent_event": ctx.event.event_id,
                "market_payload": dict(ctx.event.payload or {}),
            },
        )


def _parse_until(raw: Any) -> time | None:
    try:
        hh, mm = str(raw or "").split(":")[:2]
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return None


def register(reg) -> None:
    reg.register_factor(WatchFollowupModifier())
    reg.register_factor(UrgentReviewFactor())
