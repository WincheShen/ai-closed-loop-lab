"""决策转盘 Phase 0/1 测试：合并规则、Factor、ExitGateway、黄金用例、配置校验。"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta

import pytest

from src.central_brain.metadata_store import MemoryStore
from src.wheel.config import WheelConfigError, get_config, load_wheel_config
from src.wheel.exit_gateway import ExitGateway
from src.wheel.protocol import (
    Context,
    Decision,
    Opinion,
    PersonaPolicy,
    WheelEvent,
    is_weaker,
)
from src.wheel.registry import factor
from src.wheel.runner import run_wheel
from src.wheel.shadow import daily_shadow_report, run_exit_shadow

# ── fixtures ──────────────────────────────────────────────────

_TRADING_NOW = datetime(2026, 10, 12, 9, 40)   # 周一交易日盘中


class _DummyBus:
    def emit_order_fill(self, *a, **kw):
        pass

    def publish(self, *a, **kw):
        pass


class _MockBrain:
    def __init__(self, tmp_path):
        self.store = MemoryStore(str(tmp_path / "wheel.db"))
        self.bus = _DummyBus()
        self.events: list[dict] = []

    def log_agent_event(self, session_id, agent, event_type, payload):
        self.events.append({"agent": agent, "type": event_type, "payload": payload})


def _event(event_type: str, position_id: str, now: datetime, persona_id: str = "") -> WheelEvent:
    return WheelEvent(
        event_id=f"wev-{event_type}-{now.strftime('%H%M%S')}-{position_id}",
        type=event_type, due_at=now, persona_id=persona_id,
        subject={"position_id": position_id}, source="test",
    )


def _ctx(brain, position: dict, price: float, now: datetime = _TRADING_NOW,
         persona_id: str = "short_term_hot_rotation_v1",
         kind: str = "short", mode: str = "shadow",
         event_type: str = "clock.intraday_5m",
         is_mock: bool = False, extra: dict | None = None) -> Context:
    data = {
        "position": position,
        "quote": {"price": price, "is_mock": is_mock},
    }
    if extra:
        data.update(extra)
    return Context(
        event=_event(event_type, position["position_id"], now, persona_id),
        persona=PersonaPolicy(persona_id=persona_id, kind=kind),
        now=now, brain=brain, data=data, mode=mode,
    )


_SHORT_POS = {
    "position_id": "POS-S", "symbol": "301626", "persona_id": "short_term_hot_rotation_v1",
    "entry_price": 100.0, "stop_loss": 85.0, "target_price": 120.0,
    "entry_date": "2026-10-08", "strategy_id": "hot_sector_pullback",
    "current_qty": 1000, "status": "open", "side": "long", "name": "测试短线",
}
_ULTRA_POS = {
    "position_id": "POS-U", "symbol": "688008", "persona_id": "ultra_short_momentum_v1",
    "entry_price": 200.0, "stop_loss": 195.0, "target_price": 208.0,
    "entry_date": "2026-10-09", "strategy_id": "ultra_short_momentum",
    "current_qty": 300, "status": "open", "side": "long", "name": "测试隔夜",
}
_VALUE_POS = {
    "position_id": "POS-V", "symbol": "600809", "persona_id": "warren_buffett_v1",
    "entry_price": 123.75, "stop_loss": 92.8, "target_price": 200.0,
    "entry_date": "2026-08-03", "strategy_id": "value_investing",
    "current_qty": 800, "status": "open", "side": "long", "name": "山西汾酒",
}


def _eval(name: str, ctx: Context, params: dict | None = None) -> Opinion:
    return factor(name).evaluate(ctx, params or {})


# ── Phase 0.5 合并规则 ────────────────────────────────────────

def test_is_weaker_ordering():
    assert is_weaker("HOLD", "EXIT")
    assert is_weaker("REDUCE", "EXIT")
    assert is_weaker("HOLD", "REDUCE")
    assert not is_weaker("EXIT", "REDUCE")
    assert not is_weaker("REDUCE", "HOLD")
    assert is_weaker("EXIT", "EXIT")


def test_veto_truncates_wheel(tmp_path):
    brain = _MockBrain(tmp_path)
    cfg = get_config()
    wheel = cfg.wheels["exit_confirm"]
    # mock 行情 → floor.real_quote_required 否决，propose 不应再执行
    ctx = _ctx(brain, _SHORT_POS, 80.0, is_mock=True)
    result = run_wheel(wheel, ctx.event, ctx.persona, brain, mode="shadow",
                       preloaded=ctx.data, now=ctx.now)
    assert result.decisions[0].action == "HOLD"
    assert result.decisions[0].decided_by == "floor.real_quote_required"
    factors_run = [o.factor for o in result.trace]
    assert "rule.stop_loss" not in factors_run


def test_priority_resolve_picks_rule_over_llm(tmp_path):
    """同事件里规则提议与 LLM 提议冲突时，LLM（priority>=500）只能让步。

    rule.target_reduce(20) 提议 REDUCE + llm(500) 提议 EXIT → 取规则提议。
    """
    from src.wheel.config import WheelDef

    brain = _MockBrain(tmp_path)
    wheel = WheelDef(
        name="t_rule_vs_llm", kind="decision",
        data=("position", "quote_realtime"),
        propose=("rule.target_reduce", "llm.position_review"),
        resolve="priority",
    )
    review = {"action": "EXIT", "reason": "LLM 想清仓", "thesis_status": "weakened"}
    ctx = _ctx(brain, _SHORT_POS, 121.0, extra={"llm_review": review})  # 达目标价 120
    result = run_wheel(wheel, ctx.event, ctx.persona, brain, mode="shadow",
                       preloaded=ctx.data, now=ctx.now)
    d = result.decisions[0]
    assert d.action == "REDUCE"
    assert d.proposed_by == "rule.target_reduce"


def test_llm_proposal_only_when_no_rule(tmp_path):
    brain = _MockBrain(tmp_path)
    cfg = get_config()
    wheel = cfg.wheels["llm_position_review"]
    review = {"action": "REDUCE", "reason": "接近目标", "thesis_status": "intact"}
    ctx = _ctx(brain, _SHORT_POS, 110.0, extra={"llm_review": review})
    result = run_wheel(wheel, ctx.event, ctx.persona, brain, mode="shadow",
                       preloaded=ctx.data, now=ctx.now)
    assert result.decisions[0].action == "REDUCE"
    assert result.decisions[0].decided_by == "llm.position_review"


def test_modify_cannot_strengthen(tmp_path):
    """modify 试图把 HOLD 调成 EXIT → 被忽略并告警，结论保持 HOLD。"""
    from src.wheel import registry
    from src.wheel.config import WheelDef

    class _Strengthen:
        name = "mod.test_strengthen"
        stage = "modify"
        requires: tuple = ()
        param_keys: tuple = ()

        def evaluate(self, ctx, params):
            return Opinion(factor=self.name, verdict="modify",
                           action="EXIT", reason="试图调强")

    registry.register_factor(_Strengthen())
    try:
        brain = _MockBrain(tmp_path)
        wheel = WheelDef(
            name="t_strengthen", kind="decision",
            data=("position", "quote_realtime"),
            propose=(), modify=("mod.test_strengthen",),
        )
        ctx = _ctx(brain, _SHORT_POS, 100.0)  # 无提议 → resolved HOLD
        result = run_wheel(wheel, ctx.event, ctx.persona, brain, mode="shadow",
                           preloaded=ctx.data, now=ctx.now)
        assert result.decisions[0].action == "HOLD"
        assert result.decisions[0].decided_by == "none"
    finally:
        registry._FACTORS.pop("mod.test_strengthen", None)


# ── rule.* Factor ─────────────────────────────────────────────

def test_rule_factors_match_evaluate_hard_exit(tmp_path):
    """rule.* 与旧 evaluate_hard_exit 对每个价位给出同动作（同源函数）。"""
    from src.agents.reviewer.hard_rules import evaluate_hard_exit

    brain = _MockBrain(tmp_path)
    for pos in (_SHORT_POS, _ULTRA_POS, _VALUE_POS):
        for price in (pos["stop_loss"] - 1, pos["entry_price"] * 0.9,
                      pos["entry_price"], pos["target_price"] + 1):
            old = evaluate_hard_exit(pos, price)
            ctx = _ctx(brain, pos, price, persona_id=pos["persona_id"])
            if pos is _ULTRA_POS:
                ops = [_eval("rule.narrow_stop", ctx), _eval("rule.pulse_take_profit", ctx)]
            else:
                ops = [_eval("rule.stop_loss", ctx), _eval("rule.max_loss", ctx),
                       _eval("rule.target_reduce", ctx)]
            proposed = [o for o in ops if o.verdict == "propose"]
            if old is None:
                assert not proposed, f"{pos['symbol']}@{price} 旧=None 新={proposed}"
            else:
                assert proposed[0].action == old["action"], (pos["symbol"], price)


def test_overnight_expired_rule(tmp_path):
    brain = _MockBrain(tmp_path)
    ctx = _ctx(brain, _ULTRA_POS, 200.0, event_type="clock.1430",
               persona_id="ultra_short_momentum_v1", kind="ultra")
    op = _eval("rule.overnight_expired", ctx)
    assert op.verdict == "propose" and op.action == "EXIT"
    # 当日建仓不强平
    today_pos = {**_ULTRA_POS, "entry_date": "2026-10-12"}
    ctx2 = _ctx(brain, today_pos, 200.0, event_type="clock.1430",
                persona_id="ultra_short_momentum_v1", kind="ultra")
    assert _eval("rule.overnight_expired", ctx2).verdict == "pass"


# ── mod.stop_confirm：状态入库，重启不丢 ───────────────────────

# entry 46.29 / stop 45.13：击穿价 ~44.9 浮亏约 -3%，不触发灾难性豁免
_002487_POS = {
    **_SHORT_POS, "position_id": "POS-002487", "symbol": "002487",
    "entry_price": 46.29, "stop_loss": 45.13, "target_price": 50.0,
}


def test_stop_confirm_two_breaches_persisted(tmp_path):
    """002487 场景：击穿→HOLD 记状态；收复清除；再击穿→HOLD；连续击穿→EXIT。"""
    brain = _MockBrain(tmp_path)
    cfg = get_config()
    wheel = cfg.wheels["exit_confirm"]

    def run(price: float, minute: int) -> Decision:
        now = _TRADING_NOW.replace(minute=35) + timedelta(minutes=minute)
        ctx = _ctx(brain, _002487_POS, price, now=now)
        return run_wheel(wheel, ctx.event, ctx.persona, brain, mode="shadow",
                         preloaded=ctx.data, now=now).decisions[0]

    d1 = run(44.91, 0)   # 首次击穿 → HOLD，登记
    assert d1.action == "HOLD" and "未确认" in d1.reason
    state = brain.store.get_factor_state(
        "short_term_hot_rotation_v1", "mod.stop_confirm", "POS-002487")
    assert state and state["breach_count"] == 1

    d2 = run(46.0, 5)   # 收复 → 清除
    assert d2.action == "HOLD"
    assert brain.store.get_factor_state(
        "short_term_hot_rotation_v1", "mod.stop_confirm", "POS-002487") in (None, {})

    run(44.95, 10)       # 重新首次击穿 → HOLD
    assert brain.store.get_factor_state(
        "short_term_hot_rotation_v1", "mod.stop_confirm", "POS-002487")["breach_count"] == 1
    d4 = run(44.90, 15)  # 连续第二次击穿 → EXIT 确认
    assert d4.action == "EXIT"
    assert brain.store.get_factor_state(
        "short_term_hot_rotation_v1", "mod.stop_confirm", "POS-002487") in (None, {})


def test_stop_confirm_catastrophic_bypass(tmp_path):
    """灾难性击穿（止损下方 3%）立即放行，不等确认。"""
    brain = _MockBrain(tmp_path)
    price = 45.13 * 0.96   # ≤ stop*0.97
    ctx = _ctx(brain, _002487_POS, price)
    ctx.resolved = Decision(wheel="t", subject_key="POS-002487", action="EXIT",
                            decided_by="rule.stop_loss", risk_flag="stop_loss_triggered",
                            price=price)
    op = _eval("mod.stop_confirm", ctx)
    assert op.verdict == "pass" and "灾难性" in op.reason


def test_stop_confirm_llm_exit_at_stop_needs_confirm(tmp_path):
    """301697 场景：LLM 提议 EXIT 时价格恰逢止损位 → 同样要二次确认。"""
    brain = _MockBrain(tmp_path)
    ctx = _ctx(brain, _002487_POS, 44.9,
               extra={"llm_review": {"action": "EXIT", "reason": "走弱",
                                     "thesis_status": "weakened"}})
    cfg = get_config()
    result = run_wheel(cfg.wheels["llm_position_review"], ctx.event, ctx.persona,
                       brain, mode="shadow", preloaded=ctx.data, now=ctx.now)
    # LLM EXIT + 现价 <= stop(85) → mod.stop_confirm 降为 HOLD 并登记
    assert result.decisions[0].action == "HOLD"
    assert brain.store.get_factor_state(
        "short_term_hot_rotation_v1", "mod.stop_confirm", "POS-002487")


def test_stop_confirm_ultra_exempt(tmp_path):
    """隔夜客窄止损即时生效，不做二次确认。"""
    brain = _MockBrain(tmp_path)
    ctx = _ctx(brain, _ULTRA_POS, 194.0,
               persona_id="ultra_short_momentum_v1", kind="ultra")
    ctx.resolved = Decision(wheel="t", subject_key="POS-U", action="EXIT",
                            decided_by="rule.narrow_stop",
                            risk_flag="stop_loss_triggered", price=194.0)
    assert _eval("mod.stop_confirm", ctx).verdict == "pass"


def test_stop_confirm_llm_exit_above_stop_passes(tmp_path):
    """LLM 论点失效类 EXIT 且价格在止损上方 → 不触发二次确认。"""
    brain = _MockBrain(tmp_path)
    ctx = _ctx(brain, _SHORT_POS, 95.0)
    ctx.resolved = Decision(wheel="t", subject_key="POS-S", action="EXIT",
                            decided_by="llm.position_review", price=95.0,
                            evidence={"thesis_status": "invalidated"})
    assert _eval("mod.stop_confirm", ctx).verdict == "pass"


# ── mod.reduce_daily_cap / value_noise_filter / min_hold ──────

def test_reduce_daily_cap(tmp_path):
    brain = _MockBrain(tmp_path)
    brain.store.open_position(
        position_id="POS-S", symbol="301626", entry_price=100, qty=1000,
        entry_date="2026-10-08", persona_id="short_term_hot_rotation_v1",
    )
    # 写入一条今日 sell fill
    conn = brain.store._conn()
    conn.execute(
        """INSERT INTO fills (fill_id, order_id, symbol, side, quantity,
           avg_price, filled_at, persona_id) VALUES (?,?,?,?,?,?,?,?)""",
        ("F1", "O1", "301626", "sell", 500, 110.0,
         _TRADING_NOW.isoformat(), "short_term_hot_rotation_v1"),
    )
    conn.commit()
    ctx = _ctx(brain, _SHORT_POS, 110.0)
    ctx.resolved = Decision(wheel="t", subject_key="POS-S", action="REDUCE",
                            decided_by="rule.target_reduce", price=110.0)
    op = _eval("mod.reduce_daily_cap", ctx)
    assert op.verdict == "modify" and op.action == "HOLD"


def test_value_noise_filter_blocks_llm_exit(tmp_path):
    """600809 场景：价值持仓 -4.6% LLM EXIT（论点未失效）→ HOLD。"""
    brain = _MockBrain(tmp_path)
    ctx = _ctx(brain, _VALUE_POS, 118.0,
               persona_id="warren_buffett_v1", kind="value",
               extra={"llm_review": {"action": "EXIT", "reason": "盘中放量下跌",
                                     "thesis_status": "weakened"}})
    cfg = get_config()
    result = run_wheel(cfg.wheels["llm_position_review"], ctx.event, ctx.persona,
                       brain, mode="shadow", preloaded=ctx.data, now=ctx.now)
    assert result.decisions[0].action == "HOLD"
    assert "价值持仓" in result.decisions[0].reason


def test_value_noise_filter_allows_invalidated(tmp_path):
    brain = _MockBrain(tmp_path)
    ctx = _ctx(brain, _VALUE_POS, 118.0,
               persona_id="warren_buffett_v1", kind="value")
    ctx.resolved = Decision(wheel="t", subject_key="POS-V", action="EXIT",
                            decided_by="llm.position_review", price=118.0,
                            evidence={"thesis_status": "invalidated"})
    assert _eval("mod.value_noise_filter", ctx).verdict == "pass"


def test_value_minus8_noise_no_rule_exit(tmp_path):
    """600809 -8.7%：规则层不出（止损 92.8 未破 + 价值免-8%），LLM 噪音被拦。"""
    brain = _MockBrain(tmp_path)
    ctx = _ctx(brain, _VALUE_POS, 113.0,
               persona_id="warren_buffett_v1", kind="value",
               extra={"llm_review": {"action": "EXIT", "reason": "跌破",
                                     "thesis_status": "intact"}})
    cfg = get_config()
    r1 = run_wheel(cfg.wheels["exit_confirm"], ctx.event, ctx.persona,
                   brain, mode="shadow", preloaded=ctx.data, now=ctx.now)
    assert r1.decisions[0].action == "HOLD"
    r2 = run_wheel(cfg.wheels["llm_position_review"], ctx.event, ctx.persona,
                   brain, mode="shadow", preloaded=ctx.data, now=ctx.now)
    assert r2.decisions[0].action == "HOLD"


def test_min_hold_blocks_early_llm_reduce(tmp_path):
    brain = _MockBrain(tmp_path)
    pos = {**_SHORT_POS, "entry_date": "2026-10-10"}  # 持仓 2 天差1天
    pos["entry_date"] = "2026-10-11"
    ctx = _ctx(brain, pos, 110.0)
    ctx.resolved = Decision(wheel="t", subject_key="POS-S", action="REDUCE",
                            decided_by="llm.position_review", price=110.0)
    op = _eval("mod.min_hold", ctx)
    assert op.verdict == "modify" and op.action == "HOLD"


# ── ExitGateway ───────────────────────────────────────────────

def _open(brain, pos: dict) -> None:
    brain.store.open_position(
        position_id=pos["position_id"], symbol=pos["symbol"],
        entry_price=pos["entry_price"], qty=pos["current_qty"],
        entry_date=pos["entry_date"], persona_id=pos["persona_id"],
        stop_loss=pos.get("stop_loss"), target_price=pos.get("target_price"),
        strategy_id=pos.get("strategy_id"), name=pos.get("name", ""),
    )


def test_gateway_skips_when_position_closed(tmp_path):
    brain = _MockBrain(tmp_path)
    ctx = _ctx(brain, _SHORT_POS, 84.0)
    d = Decision(wheel="exit_confirm", subject_key="POS-S", action="EXIT",
                 decided_by="rule.stop_loss", price=84.0)
    res = ExitGateway(brain).submit(d, ctx)
    assert res["reason"] == "position_not_open"


def test_gateway_t1_lock(tmp_path):
    """600383 场景：当日 09:30 买入的持仓，09:35 不可卖出（T+1）。"""
    brain = _MockBrain(tmp_path)
    pos = {**_SHORT_POS, "entry_date": "2026-10-12"}
    _open(brain, pos)
    ctx = _ctx(brain, pos, 84.0)
    d = Decision(wheel="exit_confirm", subject_key="POS-S", action="EXIT",
                 decided_by="rule.stop_loss", price=84.0)
    res = ExitGateway(brain).submit(d, ctx)
    assert res["reason"] == "t1_locked" and not res["executed"]
    assert brain.store.get_position("POS-S")["status"] == "open"


def test_gateway_t1_veto_in_wheel(tmp_path):
    """600383 完整链路：wheel 层 floor.t_plus_1 就先否决。"""
    brain = _MockBrain(tmp_path)
    pos = {**_SHORT_POS, "entry_date": "2026-10-12"}
    _open(brain, pos)
    ctx = _ctx(brain, pos, 84.0)
    cfg = get_config()
    result = run_wheel(cfg.wheels["exit_confirm"], ctx.event, ctx.persona,
                       brain, mode="shadow", preloaded=ctx.data, now=ctx.now)
    assert result.decisions[0].action == "HOLD"
    assert result.decisions[0].decided_by == "floor.t_plus_1"


def test_gateway_no_real_price(tmp_path):
    brain = _MockBrain(tmp_path)
    _open(brain, _SHORT_POS)
    ctx = _ctx(brain, _SHORT_POS, 84.0)
    d = Decision(wheel="exit_confirm", subject_key="POS-S", action="EXIT",
                 decided_by="rule.stop_loss", price=0.0)
    res = ExitGateway(brain).submit(d, ctx)
    assert res["reason"] == "no_real_price"


def test_gateway_shadow_does_not_execute(tmp_path):
    brain = _MockBrain(tmp_path)
    _open(brain, _SHORT_POS)
    ctx = _ctx(brain, _SHORT_POS, 84.0, mode="shadow")
    d = Decision(wheel="exit_confirm", subject_key="POS-S", action="EXIT",
                 decided_by="rule.stop_loss", price=84.0)
    res = ExitGateway(brain).submit(d, ctx)
    assert res["accepted"] and not res["executed"]
    assert brain.store.get_position("POS-S")["status"] == "open"


@pytest.mark.asyncio
async def test_gateway_live_executes_and_cancels_followups(tmp_path):
    brain = _MockBrain(tmp_path)
    _open(brain, _SHORT_POS)
    brain.store.insert_wheel_event({
        "event_id": "wev-followup-1", "type": "followup.morning_watch",
        "subject": {"position_id": "POS-S"}, "source": "wheel:morning_watch",
        "due_at": _TRADING_NOW.isoformat(),
    })
    ctx = _ctx(brain, _SHORT_POS, 84.0, mode="live")
    d = Decision(wheel="exit_confirm", subject_key="POS-S", action="EXIT",
                 decided_by="rule.stop_loss", price=84.0)
    res = ExitGateway(brain).submit(d, ctx)
    assert res["executed"] is True
    assert brain.store.get_position("POS-S")["status"] == "closed"
    # 在途后续事件已作废
    row = brain.store._conn().execute(
        "SELECT status FROM wheel_events WHERE event_id='wev-followup-1'",
    ).fetchone()
    assert row["status"] == "cancelled"


@pytest.mark.asyncio
async def test_gateway_lock_and_dedup(tmp_path):
    brain = _MockBrain(tmp_path)
    _open(brain, _SHORT_POS)
    ctx = _ctx(brain, _SHORT_POS, 84.0, mode="live")
    d = Decision(wheel="exit_confirm", subject_key="POS-S", action="EXIT",
                 decided_by="rule.stop_loss", price=84.0)
    # 持仓锁：锁定期内第二条被跳过
    gw = ExitGateway(brain)
    gw._acquire("POS-S", _TRADING_NOW)
    assert gw.submit(d, ctx)["reason"] == "exit_in_progress"
    gw._release("POS-S")
    # 同窗口去重：一条已执行的 EXIT 之后，同分钟内不再执行
    brain.store.record_wheel_decision(
        decision_id="WD-DUP", event_id="e1", wheel="exit_confirm",
        persona_id="p", subject_key="POS-S", action="EXIT",
        qty_pct=None, mode="live", reason="r", now=_TRADING_NOW,
    )
    brain.store.mark_wheel_decision_executed("WD-DUP")
    res = gw.submit(d, ctx)
    assert res["reason"] == "duplicate_in_window"


# ── 黄金用例：002487 端到端（shadow 全链路，状态落库）─────────

def test_golden_002487_spike_then_recovery(tmp_path):
    """单 tick 击穿后收复：第一次扫描 HOLD，收复清除，再次连续击穿才 EXIT。"""
    brain = _MockBrain(tmp_path)
    _open(brain, {**_SHORT_POS, "position_id": "POS-002487", "stop_loss": 45.13,
                "entry_price": 46.29})
    pos = {**_SHORT_POS, "position_id": "POS-002487", "stop_loss": 45.13,
           "entry_price": 46.29}

    def scan(price, minute):
        now = _TRADING_NOW.replace(minute=35) + timedelta(minutes=minute)
        return run_exit_shadow(
            brain, pos, price=price, is_mock=False,
            event_type="clock.intraday_5m", source="test", now=now,
        )

    assert scan(44.91, 0).action == "HOLD"    # 首次击穿
    assert scan(46.00, 5).action == "HOLD"    # 收复
    assert scan(44.95, 10).action == "HOLD"   # 重新首次击穿
    assert scan(44.90, 15).action == "EXIT"   # 连续二次确认


def test_golden_002285_mock_quote_veto(tmp_path):
    """mock/无行情 → floor 否决，绝不产生卖出决策。"""
    brain = _MockBrain(tmp_path)
    pos = {**_SHORT_POS, "position_id": "POS-002285"}
    d = run_exit_shadow(
        brain, pos, price=0.0, is_mock=True,
        event_type="clock.intraday_5m", source="test",
    )
    assert d.action == "HOLD" and "floor" in d.decided_by


def test_golden_force_close_expired_and_no_quote(tmp_path):
    """隔夜到期持仓：有真实行情 → EXIT；无行情 → floor 否决（不假价成交）。"""
    brain = _MockBrain(tmp_path)
    _open(brain, _ULTRA_POS)
    d = run_exit_shadow(
        brain, _ULTRA_POS, price=200.0, is_mock=False,
        event_type="clock.1430", source="test",
        now=datetime(2026, 10, 12, 14, 30),
    )
    assert d.action == "EXIT" and d.decided_by == "rule.overnight_expired"

    d2 = run_exit_shadow(
        brain, _ULTRA_POS, price=0.0, is_mock=True,
        event_type="clock.1430", source="test",
        now=datetime(2026, 10, 12, 14, 31),
    )
    assert d2.action == "HOLD" and d2.decided_by == "floor.real_quote_required"


def test_ultra_shadow_uses_ultra_wheel(tmp_path):
    """隔夜客绑定的是 exit_confirm_ultra（窄止损/脉冲止盈），无二次确认。"""
    brain = _MockBrain(tmp_path)
    _open(brain, _ULTRA_POS)
    d = run_exit_shadow(
        brain, _ULTRA_POS, price=194.0, is_mock=False,   # ≤ stop 195 → 窄止损
        event_type="clock.intraday_5m", source="test", now=_TRADING_NOW,
    )
    assert d.action == "EXIT" and d.decided_by == "rule.narrow_stop"


def test_unbound_event_returns_none(tmp_path):
    """隔夜客不绑 clock.intraday_30m → LLM 复审对它在结构上不存在。"""
    brain = _MockBrain(tmp_path)
    d = run_exit_shadow(
        brain, _ULTRA_POS, price=200.0, is_mock=False,
        event_type="clock.intraday_30m", source="test",
        extra={"llm_review": {"action": "EXIT", "reason": "x"}},
        now=_TRADING_NOW,
    )
    assert d is None


# ── 影子对比报告 ──────────────────────────────────────────────

def test_shadow_report_classifies(tmp_path):
    brain = _MockBrain(tmp_path)
    _open(brain, {**_SHORT_POS, "position_id": "POS-A"})
    _open(brain, {**_SHORT_POS, "position_id": "POS-B", "symbol": "000001"})
    _open(brain, {**_SHORT_POS, "position_id": "POS-C", "symbol": "000002"})

    today = date.today().isoformat()
    now_iso = datetime.now().isoformat()
    store = brain.store
    store.record_wheel_decision("D1", "e1", "exit_confirm", "p", "POS-A",
                                "EXIT", None, "shadow", "r")
    store.record_wheel_decision("D2", "e2", "exit_confirm", "p", "POS-B",
                                "HOLD", None, "shadow", "r")
    store.record_wheel_decision("D3", "e3", "exit_confirm", "p", "POS-C",
                                "EXIT", None, "shadow", "r")
    # POS-A 当日平仓（legacy 也卖了）→ 一致
    store.close_position("POS-A", 84.0, -100.0, closed_at=now_iso)
    # POS-B legacy 卖了（sell fill）→ 转盘更严
    conn = store._conn()
    conn.execute(
        """INSERT INTO fills (fill_id, order_id, symbol, side, quantity,
           avg_price, filled_at, persona_id) VALUES (?,?,?,?,?,?,?,?)""",
        ("F-B", "O-B", "000001", "sell", 1000, 90.0,
         now_iso, "short_term_hot_rotation_v1"),
    )
    conn.commit()
    # POS-C 无 legacy 动作 → 转盘更松

    report = daily_shadow_report(brain, day=today)
    assert report["agree"] == 1
    assert report["wheel_stricter"] == 1
    assert report["wheel_looser"] == 1
    assert report["looser_items"][0]["position_id"] == "POS-C"


# ── Phase 0.2 配置校验 ────────────────────────────────────────

def test_wheels_yaml_loads_and_binds():
    cfg = get_config(reload=True)
    assert "exit_confirm" in cfg.wheels
    for pid in ("short_term_hot_rotation_v1", "ultra_short_momentum_v1",
                "duan_yongping_v1", "warren_buffett_v1"):
        assert cfg.binding_for(pid, "clock.intraday_5m") is not None
    assert cfg.binding_for("ultra_short_momentum_v1", "clock.1430").wheel == "overnight_force_close"
    assert cfg.binding_for("ultra_short_momentum_v1", "clock.intraday_30m") is None


def test_wheels_yaml_invalid_refs(tmp_path):
    bad = tmp_path / "wheels.yaml"
    bad.write_text(
        "wheels:\n  w1:\n    kind: decision\n    propose: [rule.nonexistent]\n"
        "personas:\n  p1:\n    kind: short\n    bindings:\n"
        "      clock.x: { wheel: w1 }\n",
    )
    with pytest.raises(WheelConfigError):
        load_wheel_config(bad)


def test_wheels_yaml_wrong_stage_rejected(tmp_path):
    bad = tmp_path / "wheels.yaml"
    bad.write_text(
        "wheels:\n  w1:\n    kind: decision\n    veto: [rule.stop_loss]\n"
        "personas: {}\n",
    )
    with pytest.raises(WheelConfigError, match="stage"):
        load_wheel_config(bad)


def test_evaluate_hard_exit_parity_after_refactor():
    """重构后 evaluate_hard_exit 行为不变（抽样校验关键阈值）。"""
    from src.agents.reviewer.hard_rules import evaluate_hard_exit

    # 短线: 止损→EXIT, -8%→EXIT, 目标→REDUCE
    assert evaluate_hard_exit(_SHORT_POS, 84.9)["action"] == "EXIT"
    assert evaluate_hard_exit(_SHORT_POS, 91.0)["risk_flag"] == "max_loss_exceeded"
    assert evaluate_hard_exit(_SHORT_POS, 121.0)["action"] == "REDUCE"
    assert evaluate_hard_exit(_SHORT_POS, 100.0) is None
    # 隔夜: 窄止损即时 EXIT，脉冲止盈 EXIT
    assert evaluate_hard_exit(_ULTRA_POS, 194.9)["action"] == "EXIT"
    assert evaluate_hard_exit(_ULTRA_POS, 208.0)["risk_flag"] == ""
    # 价值: -8.7% 不出，宽止损破→EXIT
    assert evaluate_hard_exit(_VALUE_POS, 113.0) is None
    assert evaluate_hard_exit(_VALUE_POS, 92.0)["action"] == "EXIT"


# ── 修复回归：同分钟双转盘不重复计数 / -8% 熔断窗口 / rule_override / 底线 fail-closed ──

def test_stop_confirm_same_tick_two_wheels_not_double_counted(tmp_path):
    """同一时刻 hard_scan + LLM 复审各跑一次转盘 → 只算一次击穿（A 修复）。

    10:00:00 exit_confirm 判 HOLD 登记一次；10:00:20 llm_position_review
    看到 LLM EXIT 也恰逢止损位，但观测间隔 <180s → 不计数，仍 HOLD。
    """
    brain = _MockBrain(tmp_path)
    _open(brain, _002487_POS)
    t0 = _TRADING_NOW
    d1 = run_exit_shadow(
        brain, _002487_POS, price=44.9, is_mock=False,
        event_type="clock.intraday_5m", source="hard_scan", now=t0,
    )
    assert d1.action == "HOLD"
    d2 = run_exit_shadow(
        brain, _002487_POS, price=44.9, is_mock=False,
        event_type="clock.intraday_30m", source="intraday_review",
        extra={"llm_review": {"action": "EXIT", "reason": "走弱",
                              "thesis_status": "weakened"}},
        now=t0 + timedelta(seconds=20),
    )
    assert d2.action == "HOLD"
    state = brain.store.get_factor_state(
        "short_term_hot_rotation_v1", "mod.stop_confirm", "POS-002487")
    assert state["breach_count"] == 1
    # ≥180s 后的下一次观测才计数 → 第二次有效击穿 → 确认 EXIT
    d3 = run_exit_shadow(
        brain, _002487_POS, price=44.9, is_mock=False,
        event_type="clock.intraday_30m", source="intraday_review",
        extra={"llm_review": {"action": "EXIT", "reason": "走弱",
                              "thesis_status": "weakened"}},
        now=t0 + timedelta(minutes=5),
    )
    assert d3.action == "EXIT"


def test_stop_confirm_max_loss_window_enforced(tmp_path):
    """-8% 熔断（价格仍在止损线上方）也要走确认窗口（B 修复）。"""
    brain = _MockBrain(tmp_path)
    pos = {**_SHORT_POS, "position_id": "POS-ML", "entry_price": 100.0,
           "stop_loss": 85.0, "target_price": 120.0}
    _open(brain, pos)

    def scan(now):
        return run_exit_shadow(
            brain, pos, price=91.0, is_mock=False,   # -9% 熔断，价格高于止损
            event_type="clock.intraday_5m", source="t", now=now,
        )

    assert scan(_TRADING_NOW).action == "HOLD"                       # 首次观测
    # 3 小时后仍在 -9%：距上次观测超出 15 分钟窗口 → 重新计为首次，不是确认
    assert scan(_TRADING_NOW + timedelta(hours=3)).action == "HOLD"
    # 再过 5 分钟（≥180s）仍 -9% → 第二次有效观测 → 确认
    assert scan(_TRADING_NOW + timedelta(hours=3, minutes=5)).action == "EXIT"


def test_llm_hold_does_not_wipe_breach_state(tmp_path):
    """LLM HOLD 复审恰好与 hard_scan 同时刻跑 → 不能清掉刚登记的击穿（B 修复附带）。"""
    brain = _MockBrain(tmp_path)
    _open(brain, _002487_POS)
    t0 = _TRADING_NOW
    run_exit_shadow(
        brain, _002487_POS, price=44.9, is_mock=False,
        event_type="clock.intraday_5m", source="hard_scan", now=t0,
    )
    run_exit_shadow(
        brain, _002487_POS, price=44.9, is_mock=False,
        event_type="clock.intraday_30m", source="intraday_review",
        extra={"llm_review": {"action": "HOLD", "reason": "观察"}},
        now=t0 + timedelta(seconds=20),
    )
    state = brain.store.get_factor_state(
        "short_term_hot_rotation_v1", "mod.stop_confirm", "POS-002487")
    assert state["breach_count"] == 1


def test_rule_override_goes_through_rule_factor_not_llm(tmp_path):
    """C 修复：rule_override 的 REDUCE（如超期减仓）由 rule.review_override 表达，
    不被 value_noise_filter / min_hold 误拦。"""
    brain = _MockBrain(tmp_path)
    _open(brain, _VALUE_POS)
    review = {
        "action": "REDUCE", "reason": "价值持仓 133 天浮亏，减仓释放资金",
        "risk_flag": "stale_position", "rule_override": True,
        "thesis_status": "intact", "llm_raw_action": "HOLD",
    }
    d = run_exit_shadow(
        brain, _VALUE_POS, price=113.0, is_mock=False,
        event_type="clock.intraday_30m", source="t",
        extra={"llm_review": review}, now=_TRADING_NOW,
    )
    assert d.action == "REDUCE"
    assert d.proposed_by == "rule.review_override"


def test_llm_raw_action_used_when_no_override(tmp_path):
    """C 修复：无 rule_override 时 llm.* 读 llm_raw_* 原始字段。"""
    brain = _MockBrain(tmp_path)
    _open(brain, _SHORT_POS)
    review = {
        "action": "HOLD", "reason": "min_hold 降级后的表面动作",
        "llm_raw_action": "REDUCE", "llm_raw_reason": "LLM 原本想减仓",
        "llm_raw_thesis_status": "weakened", "thesis_status": "weakened",
    }
    pos = {**_SHORT_POS, "position_id": "POS-RAW", "entry_date": "2026-10-01"}
    _open(brain, pos)
    d = run_exit_shadow(
        brain, pos, price=110.0, is_mock=False,
        event_type="clock.intraday_30m", source="t",
        extra={"llm_review": review}, now=_TRADING_NOW,
    )
    assert d.action == "REDUCE" and d.proposed_by == "llm.position_review"


def test_veto_fail_closed_on_missing_data(tmp_path):
    """D 修复：veto 阶段依赖缺失 → 否决而非放行。"""
    brain = _MockBrain(tmp_path)
    cfg = get_config()
    ev = _event("clock.intraday_5m", "NOPE", _TRADING_NOW, "short_term_hot_rotation_v1")
    persona = PersonaPolicy(persona_id="short_term_hot_rotation_v1")
    result = run_wheel(
        cfg.wheels["exit_confirm"], ev, persona, brain, mode="shadow",
        preloaded={"quote": {"price": 44.0, "is_mock": False}},
        now=_TRADING_NOW,
    )
    d = result.decisions[0]
    assert d.action == "HOLD" and d.decided_by == "floor.t_plus_1"
    veto_ops = [o for o in result.trace if o.factor == "floor.t_plus_1"]
    assert veto_ops[0].verdict == "veto"


def test_gateway_records_outcome_and_shadow_dedup(tmp_path):
    """E/P1 修复：网关结果写 wheel_decisions；shadow 下同窗口去重也生效。"""
    brain = _MockBrain(tmp_path)
    _open(brain, _002487_POS)
    t0 = _TRADING_NOW
    # 灾难性击穿 → shadow 直接放行（mark executed）
    d1 = run_exit_shadow(
        brain, _002487_POS, price=43.0, is_mock=False,
        event_type="clock.intraday_5m", source="t", now=t0,
    )
    assert d1.action == "EXIT"
    # 同一分钟内第二个卖出决策 → 网关去重拒绝
    run_exit_shadow(
        brain, _002487_POS, price=43.0, is_mock=False,
        event_type="clock.intraday_5m", source="t", now=t0 + timedelta(seconds=30),
    )
    conn = brain.store._conn()
    rows = conn.execute(
        "SELECT action, gateway, executed FROM wheel_decisions WHERE subject_key='POS-002487'",
    ).fetchall()
    sells = [dict(r) for r in rows if r["action"] in ("EXIT", "REDUCE")]
    assert any(r["gateway"] == "accepted" and r["executed"] == 1 for r in sells)
    assert any(r["gateway"] == "rejected:duplicate_in_window" for r in sells)


def test_shadow_report_uncovered_and_gateway_rejected(tmp_path):
    """报告：legacy 卖了但转盘没评估过 → uncovered；网关拒绝不计入 looser。"""
    brain = _MockBrain(tmp_path)
    today = date.today().isoformat()
    now_iso = datetime.now().isoformat()
    store = brain.store
    _open(brain, {**_SHORT_POS, "position_id": "POS-LEG", "symbol": "000003"})
    _open(brain, {**_SHORT_POS, "position_id": "POS-REJ"})
    # POS-LEG：legacy 当日平仓，转盘从未评估 → uncovered
    store.close_position("POS-LEG", 90.0, -100.0, closed_at=now_iso)
    # POS-REJ：转盘提议 EXIT 但网关拒绝 → 不算 looser
    store.record_wheel_decision(
        "D-REJ", "e1", "exit_confirm", "p", "POS-REJ",
        "EXIT", None, "shadow", "r", now=datetime.now(),
    )
    store.set_wheel_decision_gateway("D-REJ", "rejected:duplicate_in_window")

    report = daily_shadow_report(brain, day=today)
    assert report["uncovered"] == 1
    assert report["uncovered_items"][0]["position_id"] == "POS-LEG"
    assert report["gateway_rejected"] == 1
    assert report["wheel_looser"] == 0


def test_binding_param_typo_rejected(tmp_path):
    """P2 修复：binding.params 里写错参数名 → 配置校验报错。"""
    bad = tmp_path / "wheels.yaml"
    bad.write_text(
        "wheels:\n  w1:\n    kind: decision\n"
        "    data: [position, quote_realtime]\n"
        "    propose: [rule.max_loss]\n"
        "    output: exit_proposal\n"
        "personas:\n  p1:\n    kind: short\n    bindings:\n"
        "      clock.x: { wheel: w1, params: { max_loss_pt: 9 } }\n",
    )
    with pytest.raises(WheelConfigError, match="max_loss_pt"):
        load_wheel_config(bad)
    good = tmp_path / "good.yaml"
    good.write_text(
        "wheels:\n  w1:\n    kind: decision\n"
        "    data: [position, quote_realtime]\n"
        "    propose: [rule.max_loss]\n"
        "    output: exit_proposal\n"
        "personas:\n  p1:\n    kind: short\n    bindings:\n"
        "      clock.x: { wheel: w1, params: { max_loss_pct: 9 } }\n",
    )
    assert load_wheel_config(good).binding_for("p1", "clock.x").params["max_loss_pct"] == 9


# ── live 让位测试公共桩 ──

def _freeze_shadow_now(monkeypatch, now: datetime = datetime(2026, 10, 12, 10, 0)):
    """run_exit_shadow 默认取 datetime.now() — 冻结到交易日盘中，测试与星期几无关。"""
    import src.wheel.shadow as shadow_mod

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(shadow_mod, "datetime", _Frozen)


def _live_5m(monkeypatch):
    from src.wheel import config as wheel_config

    cfg_obj = wheel_config.load_wheel_config()
    pcfg = cfg_obj.personas["short_term_hot_rotation_v1"]
    pcfg.bindings["clock.intraday_5m"] = dataclasses.replace(
        pcfg.bindings["clock.intraday_5m"], mode="live",
    )
    monkeypatch.setattr(wheel_config, "_cache", cfg_obj)


def _stub_hard_scan(monkeypatch, hard_scan, *, price: float):
    monkeypatch.setattr(hard_scan, "_price_for", lambda *a, **kw: (price, False))
    monkeypatch.setattr(hard_scan, "expire_stale_signals", lambda b: 0)

    async def _no_pending(*a, **kw):
        return []
    monkeypatch.setattr(
        "src.agents.reviewer.intraday_loop._check_pending_signals", _no_pending,
    )


# ── 修复回归 2：价值豁免 / live 让位兜底 / fill 映射 / 网关超时锁 ──

def test_stop_confirm_value_max_loss_exempt(tmp_path):
    """价值持仓 -9%（止损未破）+ 论点失效 EXIT → 不被二次确认拖住。

    check_max_loss 对价值股豁免 -8% 熔断；mod.stop_confirm 的击穿判断
    必须与 rule.max_loss 同一份语义，否则论点失效的卖出被错误拖延。
    """
    brain = _MockBrain(tmp_path)
    pos = {**_VALUE_POS, "position_id": "POS-V9", "entry_price": 100.0,
           "stop_loss": 75.0, "entry_date": "2026-08-01"}
    _open(brain, pos)
    review = {
        "action": "EXIT", "reason": "财报暴雷，论点失效",
        "risk_flag": "thesis_invalidated", "rule_override": True,
        "llm_raw_action": "HOLD", "llm_raw_thesis_status": "invalidated",
    }
    d = run_exit_shadow(
        brain, pos, price=91.0, is_mock=False,   # -9%，价值股不适用熔断
        event_type="clock.intraday_30m", source="t",
        extra={"llm_review": review}, now=_TRADING_NOW,
    )
    assert d.action == "EXIT"
    assert d.proposed_by == "rule.review_override"


def test_short_max_loss_still_needs_confirm(tmp_path):
    """对照组：短线持仓 -9% 熔断仍要求二次有效观测（豁免只给价值股）。"""
    brain = _MockBrain(tmp_path)
    pos = {**_SHORT_POS, "position_id": "POS-S9"}
    _open(brain, pos)
    review = {
        "action": "EXIT", "reason": "浮亏超限", "risk_flag": "max_loss_exceeded",
        "rule_override": True, "llm_raw_action": "HOLD",
    }
    d = run_exit_shadow(
        brain, pos, price=91.0, is_mock=False,
        event_type="clock.intraday_30m", source="t",
        extra={"llm_review": review}, now=_TRADING_NOW,
    )
    assert d.action == "HOLD"


def test_llm_factor_proposes_raw_when_overridden(tmp_path):
    """rule_override 时 llm.* 仍提议 LLM 原始动作（轨迹完整），由优先级裁决。"""
    brain = _MockBrain(tmp_path)
    ctx = _ctx(brain, _SHORT_POS, 90.0, extra={"llm_review": {
        "action": "REDUCE", "rule_override": True, "reason": "目标价减仓",
        "llm_raw_action": "HOLD", "llm_raw_thesis_status": "intact",
    }})
    op = _eval("llm.position_review", ctx)
    assert op.verdict == "propose" and op.action == "HOLD"
    assert op.evidence["overridden"] is True
    # pre-LLM 硬规则短路：LLM 未被调用 → 无原始判断 → skip
    ctx2 = _ctx(brain, _SHORT_POS, 90.0, extra={"llm_review": {
        "action": "EXIT", "rule_override": True, "llm_raw_action": None,
    }})
    assert _eval("llm.position_review", ctx2).verdict == "skip"


def test_report_fill_maps_repeated_symbol_to_open_position(tmp_path):
    """同一 (persona, symbol) 反复交易：sell fill 映射到未平仓持仓，不是旧持仓。"""
    brain = _MockBrain(tmp_path)
    store = brain.store
    store.open_position(
        position_id="OLD-688008", symbol="688008", entry_price=50.0, qty=100,
        entry_date="2026-09-01", persona_id="ultra_short_momentum_v1",
    )
    store.close_position("OLD-688008", 51.0, 100.0, closed_at="2026-09-02T14:30:00")
    _open(brain, {**_ULTRA_POS, "position_id": "NEW-688008",
                  "entry_date": date.today().isoformat()})

    today = date.today().isoformat()
    now_iso = datetime.now().isoformat()
    conn = store._conn()
    conn.execute(
        """INSERT INTO fills (fill_id, order_id, symbol, side, quantity,
           avg_price, filled_at, persona_id) VALUES (?,?,?,?,?,?,?,?)""",
        ("F1", "O1", "688008", "sell", 100, 59.0, now_iso,
         "ultra_short_momentum_v1"),
    )
    conn.commit()
    store.record_wheel_decision("D1", "e1", "exit_confirm_ultra", "u",
                                "NEW-688008", "EXIT", None, "shadow", "r")
    store.set_wheel_decision_gateway("D1", "accepted")

    report = daily_shadow_report(brain, day=today)
    assert report["agree"] == 1
    assert report["wheel_looser"] == 0
    assert report["uncovered"] == 0


def test_report_fill_maps_to_closed_window_when_no_open(tmp_path):
    """无未平仓持仓时，fill 按成交日落在持仓窗口内匹配已平仓的那笔。"""
    brain = _MockBrain(tmp_path)
    store = brain.store
    store.open_position(
        position_id="OLD-688008", symbol="688008", entry_price=50.0, qty=100,
        entry_date="2026-09-01", persona_id="ultra_short_momentum_v1",
    )
    store.close_position("OLD-688008", 51.0, 100.0, closed_at="2026-09-02T14:30:00")
    # 第二笔同日开仓同日平仓（隔夜客风格）
    store.open_position(
        position_id="NEW-688008", symbol="688008", entry_price=60.0, qty=100,
        entry_date=date.today().isoformat(), persona_id="ultra_short_momentum_v1",
    )
    today = date.today().isoformat()
    store.close_position("NEW-688008", 59.0, 100.0, closed_at=f"{today}T14:30:00")

    conn = store._conn()
    conn.execute(
        """INSERT INTO fills (fill_id, order_id, symbol, side, quantity,
           avg_price, filled_at, persona_id) VALUES (?,?,?,?,?,?,?,?)""",
        ("F1", "O1", "688008", "sell", 100, 59.0, f"{today}T14:30:00",
         "ultra_short_momentum_v1"),
    )
    conn.commit()
    store.record_wheel_decision("D1", "e1", "exit_confirm_ultra", "u",
                                "NEW-688008", "EXIT", None, "shadow", "r")
    store.set_wheel_decision_gateway("D1", "accepted")

    report = daily_shadow_report(brain, day=today)
    assert report["agree"] == 1
    assert report["uncovered"] == 0


@pytest.mark.asyncio
async def test_hard_scan_live_yields_to_wheel(tmp_path, monkeypatch):
    """绑定 live：转盘产出决策并经网关执行，旧路径让位（evaluate_hard_exit 不被调用）。"""
    from src.agents.reviewer import hard_scan, stop_confirm

    stop_confirm.reset()
    _freeze_shadow_now(monkeypatch)
    brain = _MockBrain(tmp_path)
    pos = {**_SHORT_POS, "position_id": "POS-LIVE"}
    _open(brain, pos)
    _live_5m(monkeypatch)
    _stub_hard_scan(monkeypatch, hard_scan, price=80.0)   # 灾难性击穿，即时 EXIT
    called: list = []
    monkeypatch.setattr(hard_scan, "evaluate_hard_exit", lambda *a, **k: called.append(1))

    await hard_scan.run_hard_scan(
        "short_term_hot_rotation_v1", force=True, brain=brain,
    )
    assert called == []   # 旧路径未参与裁决
    row = brain.store._conn().execute(
        "SELECT mode, action, gateway FROM wheel_decisions "
        "WHERE subject_key='POS-LIVE'",
    ).fetchone()
    assert row["mode"] == "live" and row["action"] == "EXIT"
    assert row["gateway"] == "executed"
    assert brain.store.get_position("POS-LIVE")["status"] == "closed"


@pytest.mark.asyncio
async def test_hard_scan_live_falls_back_when_wheel_fails(tmp_path, monkeypatch):
    """live 绑定但转盘未产出决策（None）→ 告警 + 旧路径兜底（fail-safe）。"""
    from src.agents.reviewer import hard_scan, stop_confirm

    stop_confirm.reset()
    brain = _MockBrain(tmp_path)
    pos = {**_SHORT_POS, "position_id": "POS-FB"}
    _open(brain, pos)
    _live_5m(monkeypatch)
    _stub_hard_scan(monkeypatch, hard_scan, price=84.0)
    monkeypatch.setattr(hard_scan, "run_exit_shadow", lambda *a, **k: None)
    alerts: list = []
    monkeypatch.setattr(hard_scan, "notify_ops", lambda *a, **k: alerts.append(a))

    exits = await hard_scan.run_hard_scan(
        "short_term_hot_rotation_v1", force=True, brain=brain,
    )
    assert alerts                       # 决策缺失已告警
    assert brain.store.get_position("POS-FB")["status"] == "open"
    # 旧路径按自己的确认语义处理了首次击穿
    assert exits[0]["risk_flag"] == "stop_breach_pending"


@pytest.mark.asyncio
async def test_hard_scan_live_gateway_error_no_double_sell(tmp_path, monkeypatch):
    """网关执行中异常（可能已成交）→ 转盘仍返回决策，旧路径让位，不重复卖出。"""
    from src.agents.reviewer import hard_scan, intraday_loop, stop_confirm

    stop_confirm.reset()
    _freeze_shadow_now(monkeypatch)
    brain = _MockBrain(tmp_path)
    pos = {**_SHORT_POS, "position_id": "POS-ERR"}
    _open(brain, pos)
    _live_5m(monkeypatch)
    _stub_hard_scan(monkeypatch, hard_scan, price=80.0)
    sells: list = []

    def _exec_then_fail(self, ctx, position, decision):
        sells.append("wheel")                       # 已成交
        raise RuntimeError("database is locked")    # 成交后 close_position 前异常
    monkeypatch.setattr(ExitGateway, "_execute", _exec_then_fail)

    async def _legacy(*a, **k):
        sells.append("legacy")
        return {"executed": True}
    monkeypatch.setattr(intraday_loop, "_execute_review_action", _legacy)
    alerts: list = []
    monkeypatch.setattr("src.infra.notify.notify_ops", lambda *a, **k: alerts.append(a))

    await hard_scan.run_hard_scan(
        "short_term_hot_rotation_v1", force=True, brain=brain,
    )
    assert sells == ["wheel"]
    assert alerts and alerts[0][0] == "出场网关执行结果不确定"
    row = brain.store._conn().execute(
        "SELECT gateway FROM wheel_decisions WHERE subject_key='POS-ERR'",
    ).fetchone()
    assert row["gateway"] == "error:execute_error"
    assert ExitGateway(brain)._locked("POS-ERR", datetime(2026, 10, 12, 10, 0))


def test_report_fill_same_day_sell_then_rebuy(tmp_path):
    """同日旧仓止损卖出、下午重新买入：卖出 fill 归旧仓，新仓不出现假"更严"。"""
    brain = _MockBrain(tmp_path)
    store = brain.store
    today = date.today().isoformat()
    store.open_position(
        position_id="OLD", symbol="002487", entry_price=46.0, qty=1000,
        entry_date="2026-10-08", persona_id="short_term_hot_rotation_v1",
    )
    conn = store._conn()
    conn.execute(
        """INSERT INTO fills (fill_id, order_id, symbol, side, quantity,
           avg_price, filled_at, persona_id) VALUES (?,?,?,?,?,?,?,?)""",
        ("F1", "O1", "002487", "sell", 1000, 44.9, f"{today}T10:05:00",
         "short_term_hot_rotation_v1"),
    )
    conn.commit()
    store.close_position("OLD", 44.9, -1100.0, closed_at=f"{today}T10:05:01")
    store.open_position(
        position_id="NEW", symbol="002487", entry_price=45.5, qty=1000,
        entry_date=today, persona_id="short_term_hot_rotation_v1",
    )
    store.record_wheel_decision("D1", "e1", "exit_confirm", "p", "OLD",
                                "EXIT", None, "shadow", "r")
    store.set_wheel_decision_gateway("D1", "accepted")
    store.record_wheel_decision("D2", "e2", "exit_confirm", "p", "NEW",
                                "HOLD", None, "shadow", "r")

    report = daily_shadow_report(brain, day=today)
    assert report["agree"] == 1
    assert report["wheel_stricter"] == 0
    assert report["uncovered"] == 0


def test_gateway_timeout_keeps_lock(tmp_path, monkeypatch):
    """live 执行超时 → 锁保留至 TTL，挡住下一轮重复提交。"""
    brain = _MockBrain(tmp_path)
    _open(brain, _SHORT_POS)
    ctx = _ctx(brain, _SHORT_POS, 84.0, mode="live")
    d = Decision(wheel="exit_confirm", subject_key="POS-S", action="EXIT",
                 decided_by="rule.stop_loss", price=84.0)
    gw = ExitGateway(brain)
    monkeypatch.setattr(
        gw, "_execute",
        lambda *a, **k: {"executed": False, "reason": "execute_timeout"},
    )
    monkeypatch.setattr("src.infra.notify.notify_ops", lambda *a, **k: None)
    res = gw.submit(d, ctx)
    assert res["reason"] == "execute_timeout"
    assert gw._locked("POS-S", _TRADING_NOW) is True
    assert gw.submit(d, ctx)["reason"] == "exit_in_progress"


def test_overnight_expired_today_not_configurable(tmp_path):
    """today 只能由 ctx.now 注入，写进配置会被校验拒绝。"""
    bad = tmp_path / "wheels.yaml"
    bad.write_text(
        "wheels:\n  w1:\n    kind: decision\n"
        "    propose: [rule.overnight_expired]\n"
        "    output: exit_proposal\n"
        "personas:\n  p1:\n    kind: short\n    bindings:\n"
        "      clock.x: { wheel: w1, params: { today: '2026-01-01' } }\n",
    )
    with pytest.raises(WheelConfigError, match="today"):
        load_wheel_config(bad)
