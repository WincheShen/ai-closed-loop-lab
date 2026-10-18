"""转盘执行器 — funnel / decision 两种合并规则。

decision 转盘（每个 subject 独立执行）：
    ① veto    任一 veto → 结论 HOLD/SKIP，停止
    ② propose 收集全部提议
    ③ resolve priority：取 priority 最小的提议；同级时 EXIT > REDUCE > HOLD
    ④ modify  依次修饰结论：只能调"弱"，不能调"强"

funnel 转盘：data → screen₁ → screen₂ → … → 排序取前 N → 写入 candidates

每个转盘运行留下完整轨迹（trace → events 表；decision → wheel_decisions 表），
Factor 的 state_update / clear_state 由 runner 统一写入 wheel_factor_state。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import datetime
from typing import Any

from src.wheel import registry
from src.wheel.config import Binding, WheelConfig, WheelDef, _parse_steps
from src.wheel.protocol import (
    ACTION_STRENGTH,
    Context,
    Decision,
    Opinion,
    PersonaPolicy,
    WheelEvent,
    WheelResult,
    is_weaker,
)

logger = logging.getLogger(__name__)

HOLD = "HOLD"


def _subject_key(ctx: Context) -> str:
    subj = ctx.event.subject or {}
    return str(
        subj.get("position_id") or subj.get("symbol") or subj.get("account") or "global"
    )


def _load_state(ctx: Context, factor_name: str) -> None:
    """惰性加载该 Factor 对当前 subject 的持久状态。"""
    if factor_name in ctx.state:
        return
    try:
        ctx.state[factor_name] = (
            ctx.brain.store.get_factor_state(
                ctx.persona.persona_id, factor_name, _subject_key(ctx),
            )
            or {}
        )
    except Exception:  # noqa: BLE001
        logger.warning("factor_state 读取失败 %s/%s", factor_name, _subject_key(ctx), exc_info=True)
        ctx.state[factor_name] = {}


def _persist_state(ctx: Context, op: Opinion) -> None:
    """Factor 的持久状态请求统一落库。"""
    if not op.state_update and not op.clear_state:
        return
    store = ctx.brain.store
    try:
        if op.clear_state:
            store.clear_factor_state(ctx.persona.persona_id, op.factor, _subject_key(ctx))
            ctx.state.pop(op.factor, None)
        if op.state_update:
            merged = dict(ctx.state.get(op.factor) or {})
            merged.update(op.state_update)
            store.set_factor_state(
                ctx.persona.persona_id, op.factor, _subject_key(ctx), merged,
            )
            ctx.state[op.factor] = merged
    except Exception:  # noqa: BLE001
        logger.warning("factor_state 写入失败 %s", op.factor, exc_info=True)


def _eval(
    factor_name: str, ctx: Context, params: dict, trace: list[Opinion],
    *, fail_closed: bool = False,
) -> Opinion:
    """执行一个 Factor。

    依赖缺失或异常时：普通阶段 → skip（不能让一个依据挂掉整个转盘）；
    veto 阶段（fail_closed=True，系统底线）→ veto，缺数据绝不放行。
    """
    fail_verdict = "veto" if fail_closed else "skip"
    try:
        fac = registry.factor(factor_name)
    except KeyError:
        op = Opinion(factor=factor_name, verdict=fail_verdict, reason="未注册")
        trace.append(op)
        return op
    _load_state(ctx, factor_name)
    missing = [k for k in getattr(fac, "requires", ()) if k not in ctx.data or ctx.data.get(k) is None]
    if missing:
        op = Opinion(
            factor=factor_name, verdict=fail_verdict,
            reason=(
                f"底线依赖缺失: {','.join(missing)}" if fail_closed
                else f"缺少依赖数据: {','.join(missing)}"
            ),
        )
        trace.append(op)
        _persist_state(ctx, op)
        return op
    try:
        op = fac.evaluate(ctx, params)
    except Exception as e:  # noqa: BLE001
        logger.error("Factor %s 执行异常: %s", factor_name, e, exc_info=True)
        op = Opinion(
            factor=factor_name, verdict=fail_verdict,
            reason=f"底线检查异常: {e}" if fail_closed else f"执行异常: {e}",
        )
    trace.append(op)
    _persist_state(ctx, op)
    return op


def _run_data_steps(wheel: WheelDef, ctx: Context, trace: list[Opinion]) -> None:
    for name in wheel.data:
        try:
            step = registry.data_step(name)
        except KeyError:
            trace.append(Opinion(factor=name, verdict="skip", reason="DataStep 未注册"))
            continue
        try:
            produced = step.load(ctx, {}) or {}
        except Exception as e:  # noqa: BLE001
            logger.error("DataStep %s 执行异常: %s", name, e, exc_info=True)
            produced = {}
        for k in getattr(step, "provides", ()):
            if k in produced:
                ctx.data[k] = produced[k]


def _decision_from_veto(ctx: Context, wheel: WheelDef, op: Opinion) -> Decision:
    return Decision(
        wheel=wheel.name, subject_key=_subject_key(ctx),
        action=HOLD, reason=f"系统底线否决: {op.reason}",
        decided_by=op.factor,
        price=float((ctx.data.get("quote") or {}).get("price") or 0),
    )


def run_decision(wheel: WheelDef, ctx: Context) -> WheelResult:
    trace: list[Opinion] = []
    result = WheelResult(wheel=wheel.name, trace=trace)

    _run_data_steps(wheel, ctx, trace)

    # ① veto：任何一个说"不行"就停；底线缺数据/异常一律按否决处理
    for name in wheel.veto:
        op = _eval(name, ctx, ctx.persona.params, trace, fail_closed=True)
        if op.verdict == "veto":
            ctx.resolved = _decision_from_veto(ctx, wheel, op)
            result.decisions.append(ctx.resolved)
            return result

    # ② propose：收集全部提议
    proposals: list[Opinion] = []
    for name, args in _parse_steps(list(wheel.propose), "propose"):
        merged = {**ctx.persona.params, **args}
        op = _eval(name, ctx, merged, trace)
        if op.verdict == "propose" and op.action:
            proposals.append(op)

    # ③ resolve：priority 最小优先；同级取更强动作（卖方保守）
    if proposals:
        winner = min(
            proposals,
            key=lambda o: (o.priority, -ACTION_STRENGTH.get(o.action, 0)),
        )
        ctx.resolved = Decision(
            wheel=wheel.name, subject_key=_subject_key(ctx),
            action=winner.action or HOLD,
            qty_pct=winner.qty_pct,
            reason=winner.reason,
            decided_by=winner.factor,
            proposed_by=winner.factor,
            risk_flag=str(winner.evidence.get("risk_flag", "")),
            price=float(winner.evidence.get("price") or (ctx.data.get("quote") or {}).get("price") or 0),
            evidence=dict(winner.evidence),
        )
    else:
        ctx.resolved = Decision(
            wheel=wheel.name, subject_key=_subject_key(ctx),
            action=HOLD, reason="无提议", decided_by="none", proposed_by="none",
            price=float((ctx.data.get("quote") or {}).get("price") or 0),
        )

    # ④ modify：依次修饰，只允许调弱
    for name, args in _parse_steps(list(wheel.modify), "modify"):
        merged = {**ctx.persona.params, **args}
        op = _eval(name, ctx, merged, trace)
        if op.verdict != "modify" or not op.action:
            continue
        if not is_weaker(op.action, ctx.resolved.action):
            logger.warning(
                "[%s] modify %s 试图把 %s 调强为 %s — 已忽略",
                wheel.name, name, ctx.resolved.action, op.action,
            )
            continue
        ctx.resolved = Decision(
            wheel=wheel.name, subject_key=ctx.resolved.subject_key,
            action=op.action,
            qty_pct=ctx.resolved.qty_pct,
            reason=f"{ctx.resolved.reason} | {name}: {op.reason}",
            decided_by=name,
            proposed_by=ctx.resolved.proposed_by,
            risk_flag=ctx.resolved.risk_flag,
            price=ctx.resolved.price,
            evidence={**ctx.resolved.evidence, "modified_by": name},
        )

    result.decisions.append(ctx.resolved)
    return result


def run_funnel(wheel: WheelDef, ctx: Context) -> WheelResult:
    """funnel 转盘：data → screen* → 保留的候选写入 result.candidates。"""
    trace: list[Opinion] = []
    result = WheelResult(wheel=wheel.name, trace=trace)
    _run_data_steps(wheel, ctx, trace)

    candidates = list(ctx.data.get("candidates") or [])
    for name, args in _parse_steps(list(wheel.steps), "steps"):
        merged = {**ctx.persona.params, **args}
        op = _eval(name, ctx, merged, trace)
        kept = op.evidence.get("kept")
        if isinstance(kept, list):
            candidates = kept
        # screen 的意见（含淘汰原因）全部进入轨迹
    result.candidates = candidates
    return result


def run_wheel(
    wheel: WheelDef,
    event: WheelEvent,
    persona: PersonaPolicy,
    brain,
    *,
    mode: str = "shadow",
    preloaded: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> WheelResult:
    """运行一个转盘：构建 Context → 执行 → 持久化决策与轨迹。"""
    ctx = Context(
        event=event, persona=persona,
        now=now or datetime.now(),
        brain=brain,
        data=dict(preloaded or {}),
        mode=mode,
    )
    result = (
        run_decision(wheel, ctx) if wheel.kind == "decision" else run_funnel(wheel, ctx)
    )

    subject = _subject_key(ctx)
    result.decisions = [
        replace(d, decision_id=_record_decision(
            brain, event, d, subject, mode, ctx.persona.persona_id, now=ctx.now,
        ))
        for d in result.decisions
    ]
    _log_trace(brain, event, wheel, subject, result, mode)

    if wheel.output == "exit_proposal" and result.decisions:
        from src.wheel.exit_gateway import ExitGateway
        d = result.decisions[0]
        gw = ExitGateway(brain).submit(d, ctx, decision_id=d.decision_id)
        if d.action in ("EXIT", "REDUCE") and d.decision_id:
            reason = str(gw.get("reason", ""))
            outcome = (
                "executed" if gw.get("executed")
                else f"error:{reason}" if reason in ("execute_error", "execute_timeout")
                else "accepted" if gw.get("accepted")
                else f"rejected:{reason}"
            )
            _record_gateway(brain, d.decision_id, outcome)

    result.follow_ups = [
        op.follow_up for op in result.trace if op.follow_up is not None
    ]
    if result.follow_ups:
        # 后续事件落库（shadow/live 都登记 — 时段/次数/作废约束由这里统一执行）
        try:
            from src.wheel.dispatcher import enqueue_follow_ups
            enqueue_follow_ups(brain.store, event, result.follow_ups, ctx.now)
        except Exception:  # noqa: BLE001
            logger.warning("[Wheel] 后续事件登记失败 %s", subject, exc_info=True)
    return result


def _record_decision(
    brain, event: WheelEvent, d: Decision, subject: str, mode: str,
    persona_id: str, *, now: datetime | None = None,
) -> str:
    try:
        decision_id = f"WD-{uuid.uuid4().hex[:10].upper()}"
        brain.store.record_wheel_decision(
            decision_id=decision_id,
            event_id=event.event_id,
            wheel=d.wheel,
            persona_id=persona_id,
            subject_key=subject,
            action=d.action,
            qty_pct=d.qty_pct,
            mode=mode,
            reason=d.reason,
            decided_by=d.decided_by,
            now=now,
        )
        return decision_id
    except Exception:  # noqa: BLE001
        logger.warning("wheel_decision 写入失败 %s", subject, exc_info=True)
        return ""


def _record_gateway(brain, decision_id: str, outcome: str) -> None:
    try:
        brain.store.set_wheel_decision_gateway(decision_id, outcome)
    except Exception:  # noqa: BLE001
        logger.warning("wheel_decision 网关结果写入失败 %s", decision_id, exc_info=True)


def _log_trace(
    brain, event: WheelEvent, wheel: WheelDef, subject: str,
    result: WheelResult, mode: str,
) -> None:
    """完整轨迹写 events 表 — 监控页按事件展开即可看到为什么卖/为什么没买。"""
    if not hasattr(brain, "log_agent_event"):
        return
    try:
        brain.log_agent_event(
            session_id=event.event_id,
            agent="wheel",
            event_type="wheel_completed",
            payload={
                "wheel": wheel.name,
                "mode": mode,
                "event_type": event.type,
                "persona_id": event.persona_id,
                "subject": subject,
                "decisions": [
                    {
                        "action": d.action, "decided_by": d.decided_by,
                        "reason": d.reason, "price": d.price,
                        "risk_flag": d.risk_flag,
                    }
                    for d in result.decisions
                ],
                "trace": [
                    {
                        "factor": o.factor, "verdict": o.verdict,
                        "action": o.action, "priority": o.priority,
                        "reason": o.reason,
                    }
                    for o in result.trace
                ],
            },
        )
    except Exception:  # noqa: BLE001
        logger.debug("wheel_completed 事件写入失败", exc_info=True)


def run_binding(
    cfg: WheelConfig,
    binding: Binding,
    event: WheelEvent,
    persona: PersonaPolicy,
    brain,
    *,
    preloaded: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> list[WheelResult]:
    """按绑定执行：wheel 或 chain（同事件内多个转盘依次跑，仍经数据库接力）。"""
    names = binding.chain or (binding.wheel,)
    results = []
    for wname in names:
        wheel = cfg.wheels[wname]
        results.append(
            run_wheel(
                wheel, event, persona, brain,
                mode=binding.mode, preloaded=preloaded, now=now,
            ),
        )
    return results
