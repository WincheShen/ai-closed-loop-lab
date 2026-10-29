"""Phase 2 事件层测试：Dispatcher 约束、后续事件链、市场事件广播、报告隔离。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.central_brain.metadata_store import MemoryStore
from src.wheel.dispatcher import (
    dispatch_once,
    enqueue_follow_ups,
    recover_running_events,
)
from src.wheel.protocol import FollowUp, WheelEvent
from src.wheel.shadow import run_exit_shadow


class _MockBrain:
    def __init__(self, tmp_path):
        self.store = MemoryStore(str(tmp_path / "w.db"))
        self.events = []

    def log_agent_event(self, session_id, agent, event_type, payload):
        self.events.append({"agent": agent, "type": event_type})


_ULTRA_POS = {
    "position_id": "POS-U", "symbol": "688008", "persona_id": "ultra_short_momentum_v1",
    "entry_price": 200.0, "stop_loss": 195.0, "target_price": 208.0,
    "entry_date": "2026-10-09", "strategy_id": "ultra_short_momentum",
    "current_qty": 300, "status": "open", "name": "测试隔夜",
}

_T = datetime(2026, 10, 12, 9, 35)   # 周一盘中


@pytest.fixture(autouse=True)
def _no_network_quotes(monkeypatch):
    """测试不访问网络：行情接口一律失败；每个测试独立的行情缓存（不泄漏）。"""
    from src.stock_analyzer.data_source.intraday_client import IntradayClient
    from src.wheel.data.steps import QuoteRealtimeStep

    def _boom(*a, **k):
        raise RuntimeError("测试禁用网络行情")

    monkeypatch.setattr(IntradayClient, "fetch_intraday_snapshot", _boom)
    monkeypatch.setattr(QuoteRealtimeStep, "_cache", {})


def _seed_quote(symbol: str, at: datetime, price: float) -> None:
    from src.wheel.data.steps import QuoteRealtimeStep
    QuoteRealtimeStep._cache[f"{symbol}:{at:%Y%m%d%H%M}"] = {
        "price": price, "is_mock": False, "symbol": symbol,
    }


def _open(brain, pos=None):
    p = pos or _ULTRA_POS
    brain.store.open_position(
        position_id=p["position_id"], symbol=p["symbol"], entry_price=p["entry_price"],
        qty=p["current_qty"], entry_date=p["entry_date"], persona_id=p["persona_id"],
        stop_loss=p.get("stop_loss"), target_price=p.get("target_price"),
        strategy_id=p.get("strategy_id"), name=p.get("name", ""),
    )


def _fu(**kw):
    d = dict(
        type="followup.morning_watch", after=timedelta(seconds=60),
        subject={"position_id": "POS-U"}, payload={"persona_id": "ultra_short_momentum_v1"},
        max_attempts=5,
    )
    d.update(kw)
    return FollowUp(**d)


def _parent(attempt=1):
    return WheelEvent(
        event_id="pev-1", type="clock.0935", due_at=_T, attempt=attempt,
        persona_id="ultra_short_momentum_v1", subject={"position_id": "POS-U"},
    )


# ── enqueue_follow_ups 约束（§3.3）────────────────────────────

def test_enqueue_followup_basic(tmp_path):
    brain = _MockBrain(tmp_path)
    _open(brain)
    n = enqueue_follow_ups(brain.store, _parent(), [_fu()], _T)
    assert n == 1
    rows = brain.store.list_wheel_events()
    assert len(rows) == 1 and rows[0]["type"] == "followup.morning_watch"
    assert rows[0]["attempt"] == 2 and rows[0]["status"] == "due"


def test_enqueue_followup_constraints(tmp_path):
    brain = _MockBrain(tmp_path)
    _open(brain)
    # attempt 超上限 → 不登记
    assert enqueue_follow_ups(brain.store, _parent(attempt=5), [_fu()], _T) == 0
    # 持仓已平 → 不登记
    brain.store.close_position("POS-U", 201.0, 300.0)
    assert enqueue_follow_ups(brain.store, _parent(), [_fu()], _T) == 0


def test_enqueue_followup_inflight_dedupe(tmp_path):
    brain = _MockBrain(tmp_path)
    _open(brain)
    assert enqueue_follow_ups(brain.store, _parent(), [_fu()], _T) == 1
    # 同类后续事件在途 → 不重复登记
    assert enqueue_follow_ups(brain.store, _parent(), [_fu()], _T) == 0


def test_enqueue_followup_past_until(tmp_path):
    brain = _MockBrain(tmp_path)
    _open(brain)
    from datetime import time as _time
    fu = _fu(until=_time(10, 0), after=timedelta(minutes=40))  # 09:35+40m=10:15 > 10:00
    assert enqueue_follow_ups(brain.store, _parent(), [fu], _T) == 0


def test_enqueue_followup_lunch_defers(tmp_path):
    brain = _MockBrain(tmp_path)
    _open(brain)
    # due 落在午休 → 顺延 13:00
    from datetime import time as _time
    fu = _fu(until=_time(14, 30), after=timedelta(minutes=2))
    n = enqueue_follow_ups(
        brain.store, _parent(), [fu], _T.replace(hour=11, minute=29),
    )
    assert n == 1
    row = brain.store.list_wheel_events()[0]
    assert row["due_at"].startswith("2026-10-12T13:00")


# ── dispatch_once ─────────────────────────────────────────────

def test_dispatch_followup_runs_wheel(tmp_path, monkeypatch):
    brain = _MockBrain(tmp_path)
    _open(brain)
    enqueue_follow_ups(brain.store, _parent(), [_fu()], _T)
    first_id = brain.store.list_wheel_events()[0]["event_id"]
    stats = dispatch_once(brain, now=_T + timedelta(minutes=1))
    assert stats["done"] == 1
    row = next(r for r in brain.store.list_wheel_events() if r["event_id"] == first_id)
    assert row["status"] == "done"
    # morning_watch 转盘影子决策已记录（无行情 → floor 否决 HOLD，但轨迹在）
    dec = brain.store._conn().execute(
        "SELECT wheel, mode FROM wheel_decisions WHERE subject_key='POS-U'"
    ).fetchall()
    assert dec and all(r["wheel"] == "morning_watch" and r["mode"] == "shadow" for r in dec)


def test_dispatch_cancelled_when_position_closed(tmp_path):
    brain = _MockBrain(tmp_path)
    _open(brain)
    enqueue_follow_ups(brain.store, _parent(), [_fu()], _T)
    brain.store.close_position("POS-U", 201.0, 300.0)
    stats = dispatch_once(brain, now=_T + timedelta(minutes=1))
    assert stats["cancelled"] == 1


def test_dispatch_expired_and_deferred(tmp_path):
    brain = _MockBrain(tmp_path)
    _open(brain)
    # 午休期间到期 → 顺延（deferred）
    enqueue_follow_ups(brain.store, _parent(), [_fu(until=None)], _T)
    brain.store.defer_wheel_event(
        brain.store.list_wheel_events()[0]["event_id"], _T.replace(hour=12, minute=0),
    )
    stats = dispatch_once(brain, now=_T.replace(hour=12, minute=0))
    assert stats["deferred"] == 1
    # 收盘后到期 → expired
    stats = dispatch_once(brain, now=_T.replace(hour=15, minute=30))
    assert stats["expired"] == 1


def test_dispatch_market_event_broadcast(tmp_path):
    """market.regime_drift 广播 → 绑定角色对在持持仓记 REVIEW；隔夜客不绑 LLM 复审，不响应。"""
    brain = _MockBrain(tmp_path)
    _open(brain)  # POS-U: ultra_short_momentum_v1（不绑定 market.regime_drift）
    _open(brain, {**_ULTRA_POS, "position_id": "POS-S", "symbol": "000001",
                  "persona_id": "short_term_hot_rotation_v1"})
    _open(brain, {**_ULTRA_POS, "position_id": "POS-V", "symbol": "600519",
                  "persona_id": "warren_buffett_v1"})
    brain.store.insert_wheel_event({
        "event_id": "md-1", "type": "market.regime_drift", "persona_id": None,
        "subject": {}, "payload": {"to_regime": "panic"},
        "source": "drift_detector", "due_at": _T.isoformat(),
    })
    stats = dispatch_once(brain, now=_T)
    assert stats["done"] == 1
    rows = brain.store._conn().execute(
        "SELECT subject_key, action, mode FROM wheel_decisions WHERE wheel='urgent_review'"
    ).fetchall()
    keys = {r["subject_key"] for r in rows}
    # short_term 与 buffett 持仓被复审，隔夜客 POS-U 不在其中
    assert keys == {"POS-S", "POS-V"}
    assert "POS-U" not in keys
    assert all(r["action"] == "REVIEW" and r["mode"] == "shadow" for r in rows)


def test_dispatch_non_wheel_event_type_skipped(tmp_path):
    """非本阶段接管的事件类型 → cancelled，不进转盘。"""
    brain = _MockBrain(tmp_path)
    brain.store.insert_wheel_event({
        "event_id": "clk-1", "type": "clock.intraday_5m", "persona_id": None,
        "subject": {}, "payload": {}, "source": "t", "due_at": _T.isoformat(),
    })
    stats = dispatch_once(brain, now=_T)
    assert stats["skipped"] == 1


def test_recover_running_events(tmp_path):
    brain = _MockBrain(tmp_path)
    brain.store.insert_wheel_event({
        "event_id": "st-1", "type": "followup.morning_watch", "persona_id": "u",
        "subject": {"position_id": "POS-U"}, "payload": {},
        "source": "t", "due_at": _T.isoformat(),
    })
    brain.store.set_wheel_event_status("st-1", "running")
    brain.store._conn().execute(
        "UPDATE wheel_events SET started_at=? WHERE event_id='st-1'",
        ((datetime(2026, 10, 12, 9, 0)).isoformat(),),
    )
    brain.store._conn().commit()
    assert recover_running_events(brain) == 1
    assert brain.store.list_wheel_events()[0]["status"] == "due"


# ── 端到端：clock.0935 → followup 链 ─────────────────────────

def test_morning_watch_chains_followups(tmp_path, monkeypatch):
    """09:35 影子评估 → 登记后续事件 → dispatcher 触发再评估 → 链式推进。"""
    brain = _MockBrain(tmp_path)
    _open(brain)
    # 第一次：pulse 评估（真实价 +0.5% 不触发任何卖出规则 → HOLD → 登记后续）
    d = run_exit_shadow(
        brain, _ULTRA_POS, price=201.0, is_mock=False,
        event_type="clock.0935", source="morning_pulse", now=_T,
    )
    assert d is not None
    rows = brain.store.list_wheel_events()
    assert len(rows) == 1 and rows[0]["attempt"] == 2

    # dispatcher 触发后续 → 再跑 morning_watch → 再登记下一条。
    # 测试无网络：预置行情缓存让 data.quote_realtime 返回真实价
    _seed_quote("688008", _T + timedelta(minutes=1), 201.0)
    dispatch_once(brain, now=_T + timedelta(minutes=1))
    rows = brain.store.list_wheel_events()
    due = [r for r in rows if r["status"] == "due"]
    assert due and due[0]["attempt"] == 3


def test_gate_report_ignores_phase2_wheels(tmp_path):
    """morning_watch/urgent_review 的影子决策不进 looser/stricter 门槛统计。"""
    from src.wheel.shadow import daily_shadow_report
    brain = _MockBrain(tmp_path)
    _open(brain)
    today = _T.date().isoformat()
    brain.store.record_wheel_decision(
        "D1", "e1", "morning_watch", "u", "POS-U", "EXIT", None, "shadow", "r",
        now=_T,
    )
    report = daily_shadow_report(brain, day=today)
    assert report["wheel_looser"] == 0
    assert report["other_wheels"] == {"morning_watch": 1}


# ── 回审修复 ─────────────────────────────────────────────────

def _decisions(brain):
    return [dict(r) for r in brain.store._conn().execute(
        "SELECT wheel, action, gateway, executed, mode FROM wheel_decisions ORDER BY created_at"
    )]


def test_shadow_dedup_isolated_from_gate_wheels(tmp_path):
    """P0：morning_watch 的影子 EXIT 不能去重掉门槛转盘 exit_confirm_ultra，
    否则"转盘更松"会被掩盖成"网关拒绝"。"""
    from src.wheel.shadow import daily_shadow_report
    brain = _MockBrain(tmp_path)
    _open(brain)
    p = brain.store.get_position("POS-U")
    run_exit_shadow(brain, p, price=194.0, is_mock=False, event_type="followup.morning_watch",
                    source="dispatcher", now=_T.replace(minute=39, second=30),
                    mode_override="shadow")
    run_exit_shadow(brain, p, price=194.0, is_mock=False, event_type="clock.intraday_5m",
                    source="hard_scan", now=_T.replace(minute=40),
                    mode_override="shadow")
    rows = _decisions(brain)
    assert [r["wheel"] for r in rows] == ["morning_watch", "exit_confirm_ultra"]
    assert rows[1]["action"] == "EXIT" and rows[1]["gateway"] == "accepted"
    rep = daily_shadow_report(brain, day=_T.date().isoformat())
    # 旧路径没卖 → 门槛转盘的 EXIT 必须如实计为"更松"，不能被藏进网关拒绝
    assert rep["wheel_looser"] == 1 and rep["gateway_rejected"] == 0


def test_shadow_dedup_still_works_within_gate_cohort(tmp_path):
    """门槛转盘之间的同窗口去重保留（影子语义与 live 一致）。"""
    brain = _MockBrain(tmp_path)
    _open(brain)
    p = brain.store.get_position("POS-U")
    for sec in (0, 20):
        run_exit_shadow(brain, p, price=194.0, is_mock=False, event_type="clock.intraday_5m",
                        source="hard_scan", now=_T.replace(minute=40, second=sec),
                        mode_override="shadow")
    rows = _decisions(brain)
    assert rows[0]["gateway"] == "accepted"
    assert rows[1]["gateway"] == "rejected:duplicate_in_window"


def test_live_dedup_ignores_shadow_decisions(tmp_path):
    """live 去重只看 live 决策：影子"会执行"不能挡掉真实卖出。"""
    from src.wheel.exit_gateway import ExitGateway
    from src.wheel.protocol import Decision
    brain = _MockBrain(tmp_path)
    _open(brain)
    t = _T.replace(minute=40)
    brain.store.record_wheel_decision(
        "WD-S", "e", "morning_watch", "ultra_short_momentum_v1", "POS-U", "EXIT", None,
        "shadow", "r", now=t - timedelta(seconds=10),
    )
    brain.store.mark_wheel_decision_executed("WD-S")
    gw = ExitGateway(brain)
    d = Decision(wheel="exit_confirm_ultra", subject_key="POS-U", action="EXIT", price=194.0)
    assert gw._duplicate_in_window("POS-U", d, t, mode="live") is False
    # 同一条影子决策在 shadow 模式下对同组（morning_watch 自身）仍去重
    d2 = Decision(wheel="morning_watch", subject_key="POS-U", action="EXIT", price=194.0)
    assert gw._duplicate_in_window("POS-U", d2, t, mode="shadow") is True


def test_followup_survives_transient_quote_veto(tmp_path):
    """P1：行情暂时不可用被底线否决 → 仍登记下一次复查（链条不断）；
    T+1 等非暂时性否决不登记。"""
    brain = _MockBrain(tmp_path)
    _open(brain)
    d = run_exit_shadow(brain, _ULTRA_POS, price=0.0, is_mock=True,
                        event_type="clock.0935", source="morning_pulse", now=_T)
    assert d is not None and d.action == "HOLD" and d.decided_by == "floor.real_quote_required"
    rows = brain.store.list_wheel_events()
    assert len(rows) == 1 and rows[0]["attempt"] == 2

    (tmp_path / "b2").mkdir(exist_ok=True)
    brain2 = _MockBrain(tmp_path / "b2")
    today_pos = {**_ULTRA_POS, "entry_date": _T.date().isoformat()}
    _open(brain2, today_pos)
    run_exit_shadow(brain2, today_pos, price=0.0, is_mock=True,
                    event_type="clock.0935", source="morning_pulse", now=_T)
    assert brain2.store.list_wheel_events() == []


def test_followup_chain_capped_under_repeated_quote_failure(tmp_path):
    """行情持续失败时链条按 mod.watch_followup 的 max_attempts（默认 30）收敛。"""
    brain = _MockBrain(tmp_path)
    _open(brain)
    run_exit_shadow(brain, _ULTRA_POS, price=0.0, is_mock=True,
                    event_type="clock.0935", source="morning_pulse", now=_T)
    t = _T
    for _ in range(40):
        t = t + timedelta(minutes=1)
        dispatch_once(brain, now=t)
    rows = brain.store.list_wheel_events(limit=500)
    assert max(r["attempt"] for r in rows) == 30
    assert not [r for r in rows if r["status"] == "due"]


def test_broadcast_isolates_per_position_failure(tmp_path, monkeypatch):
    """urgent_review 广播：单个持仓异常不影响其余持仓，事件记 failed。"""
    import src.wheel.dispatcher as disp
    brain = _MockBrain(tmp_path)
    _open(brain, {**_ULTRA_POS, "position_id": "POS-A", "symbol": "000001",
                  "persona_id": "short_term_hot_rotation_v1"})
    _open(brain, {**_ULTRA_POS, "position_id": "POS-B", "symbol": "000002",
                  "persona_id": "short_term_hot_rotation_v1"})
    real = disp._run_bound

    def _flaky(cfg, binding, event, pid, brain_, now, subject=None):
        if subject and subject.get("position_id") == "POS-A":
            raise RuntimeError("boom")
        return real(cfg, binding, event, pid, brain_, now, subject=subject)

    monkeypatch.setattr(disp, "_run_bound", _flaky)
    brain.store.insert_wheel_event({
        "event_id": "md-2", "type": "market.regime_drift", "persona_id": None,
        "subject": {}, "payload": {}, "source": "drift_detector", "due_at": _T.isoformat(),
    })
    stats = dispatch_once(brain, now=_T)
    assert stats["failed"] == 1
    keys = {r["subject_key"] for r in brain.store._conn().execute(
        "SELECT subject_key FROM wheel_decisions WHERE wheel='urgent_review'")}
    assert "POS-B" in keys and "POS-A" not in keys
    row = brain.store.list_wheel_events()[0]
    assert row["status"] == "failed" and "POS-A" in (row["error"] or "")


def test_wheel_output_validated(tmp_path):
    """output 拼错 → 启动校验拒绝（否则静默跳过网关）。"""
    from src.wheel.config import WheelConfigError, load_wheel_config
    bad = tmp_path / "wheels.yaml"
    bad.write_text(
        "wheels:\n  w1:\n    kind: decision\n"
        "    data: [position, quote_realtime]\n"
        "    propose: [rule.max_loss]\n"
        "    output: exit_propsal\n"
        "personas: {}\n",
    )
    with pytest.raises(WheelConfigError, match="output"):
        load_wheel_config(bad)


# ── 架构回审 1–7 单元测试 ─────────────────────────────────────

def test_exit_gateway_live_ultra_rebound_watch(tmp_path, monkeypatch):
    """修复 1：隔夜客盘中转盘 live 平仓且 pnl >= -3.5% → 自动回流自选池；
    亏损过深 (< -3.5%) 或 14:30 强平不回流。"""
    from src.wheel.exit_gateway import ExitGateway
    from src.wheel.protocol import Context, Decision, PersonaPolicy

    brain = _MockBrain(tmp_path)
    _open(brain, _ULTRA_POS)  # entry_price=200.0
    gw = ExitGateway(brain)
    monkeypatch.setattr(gw, "_execute", lambda *a, **k: {"executed": True, "reason": "ok"})

    ctx = Context(
        event=_parent(),
        persona=PersonaPolicy(persona_id="ultra_short_momentum_v1", kind="ultra"),
        now=_T,
        brain=brain,
        mode="live",
    )
    # 1) 14:30 overnight_force_close 平仓 → 不回流
    d_force = Decision(
        wheel="overnight_force_close", subject_key="POS-U",
        action="EXIT", price=202.0, reason="force",
    )
    gw.submit(d_force, ctx, decision_id="D-FC")
    assert brain.store.get_watchlist() == []

    # 2) 盘中亏损 -4.0% (< -3.5%) → 不回流
    d_deep_loss = Decision(
        wheel="exit_confirm_ultra", subject_key="POS-U",
        action="EXIT", price=192.0, reason="stop",
    )
    gw.submit(d_deep_loss, ctx, decision_id="D-LOSS")
    assert brain.store.get_watchlist() == []

    # 3) 盘中微跌 -1.0% (>= -3.5%) → 自动回流自选池 (source=rebound_watch)
    def _exec_and_close(ctx_, pos_, dec_):
        brain.store.close_position(pos_["position_id"], dec_.price, -600.0)
        return {"executed": True, "reason": "ok"}

    monkeypatch.setattr(gw, "_execute", _exec_and_close)
    d_ok = Decision(
        wheel="morning_watch", subject_key="POS-U",
        action="EXIT", price=198.0, reason="stop",
    )
    res = gw.submit(d_ok, ctx, decision_id="D-OK")
    assert res["executed"] is True
    wl = brain.store.get_watchlist()
    assert len(wl) == 1
    assert wl[0]["symbol"] == "688008" and wl[0]["source"] == "rebound_watch"


def test_exit_gateway_atomic_lock_and_release_on_reject(tmp_path):
    """修复 2：原子抢锁在同一 TTL 窗口内互斥；前置校验拒绝时释放锁。"""
    from src.wheel.exit_gateway import LOCK_TTL, ExitGateway
    from src.wheel.protocol import Context, Decision, PersonaPolicy

    brain = _MockBrain(tmp_path)
    # 同一毫秒两次原子抢锁：第一次 True，第二次 False，TTL 过后恢复 True
    assert brain.store.try_acquire_factor_lock("*", "exit_gateway.lock", "POS-U", _T, LOCK_TTL) is True
    assert brain.store.try_acquire_factor_lock("*", "exit_gateway.lock", "POS-U", _T, LOCK_TTL) is False
    assert brain.store.try_acquire_factor_lock(
        "*", "exit_gateway.lock", "POS-U", _T + LOCK_TTL + timedelta(seconds=1), LOCK_TTL,
    ) is True
    brain.store.clear_factor_state("*", "exit_gateway.lock", "POS-U")

    # live 模式下因 T+1 被拒后，持仓锁必须已释放
    today_pos = {**_ULTRA_POS, "entry_date": _T.date().isoformat()}
    _open(brain, today_pos)
    gw = ExitGateway(brain)
    ctx = Context(
        event=_parent(),
        persona=PersonaPolicy(persona_id="ultra_short_momentum_v1", kind="ultra"),
        now=_T,
        brain=brain,
        mode="live",
    )
    d = Decision(wheel="exit_confirm_ultra", subject_key="POS-U", action="EXIT", price=194.0)
    res = gw.submit(d, ctx)
    assert res["reason"] == "t1_locked"
    assert gw._locked("POS-U", _T) is False


def test_is_weaker_direction_guard_and_llm_add_normalized(tmp_path):
    """修复 3：is_weaker 禁止跨买卖方向篡改；盘中复审 LLM ADD 归一化为 HOLD。"""
    from src.wheel.factors.llm_factors import LlmPositionReviewFactor
    from src.wheel.protocol import Context, PersonaPolicy, is_weaker

    # 卖出方向内调弱合法
    assert is_weaker("REDUCE", "EXIT") is True
    assert is_weaker("HOLD", "EXIT") is True
    assert is_weaker("HOLD", "REDUCE") is True
    # 跨方向（EXIT/REDUCE -> ADD/BUY 或反之）一律非法
    assert is_weaker("ADD", "EXIT") is False
    assert is_weaker("BUY", "REDUCE") is False
    assert is_weaker("EXIT", "BUY") is False
    assert is_weaker("REDUCE", "ADD") is False
    # 买入方向内调弱合法
    assert is_weaker("HOLD", "BUY") is True
    assert is_weaker("WATCH", "ADD") is True

    brain = _MockBrain(tmp_path)
    ctx = Context(
        event=_parent(),
        persona=PersonaPolicy(persona_id="short_term_hot_rotation_v1", kind="short"),
        now=_T,
        brain=brain,
        data={
            "position": _ULTRA_POS,
            "llm_review": {"action": "ADD", "llm_raw_action": "ADD", "reason": "回调加仓"},
        },
    )
    op = LlmPositionReviewFactor().evaluate(ctx, {})
    assert op.verdict == "propose"
    assert op.action == "HOLD"
    assert op.evidence["llm_raw_action"] == "ADD"


def test_llm_review_step_self_invokes_and_timeout_180s(tmp_path, monkeypatch):
    """修复 4：LlmReviewStep 在 live 无预载时调用 PositionReviewAgent；LLM 绑定超时为 180s。"""
    from src.agents.reviewer.position_reviewer import PositionReviewAgent
    from src.wheel.config import get_config
    from src.wheel.data.steps import LlmReviewStep
    from src.wheel.dispatcher import _timeout_for_bindings
    from src.wheel.protocol import Context, PersonaPolicy

    cfg = get_config(reload=True)
    b_llm = cfg.binding_for("short_term_hot_rotation_v1", "clock.intraday_30m")
    b_watch = cfg.binding_for("ultra_short_momentum_v1", "clock.0935")
    assert _timeout_for_bindings(cfg, [("short_term_hot_rotation_v1", b_llm)]) == 180
    assert _timeout_for_bindings(cfg, [("ultra_short_momentum_v1", b_watch)]) == 60

    called = {}

    def _fake_review(self, pos, force_review_reason=None):
        called["pos_id"] = pos["position_id"]
        called["reason"] = force_review_reason
        return {"action": "REDUCE", "llm_raw_action": "REDUCE", "reason": "减仓"}

    monkeypatch.setattr(PositionReviewAgent, "review_position", _fake_review)
    brain = _MockBrain(tmp_path)
    ev = WheelEvent(
        event_id="ev-llm", type="market.regime_drift", due_at=_T,
        persona_id="short_term_hot_rotation_v1",
        subject={"position_id": "POS-S"},
        payload={"reason": "regime_drift_to_panic"},
    )
    ctx = Context(
        event=ev,
        persona=PersonaPolicy(persona_id="short_term_hot_rotation_v1", kind="short"),
        now=_T,
        brain=brain,
        data={"position": {**_ULTRA_POS, "position_id": "POS-S"}},
        mode="live",
    )
    out = LlmReviewStep().load(ctx, {})
    assert called == {"pos_id": "POS-S", "reason": "regime_drift_to_panic"}
    assert out["llm_review"]["action"] == "REDUCE"


def test_data_step_params_and_session_quote_cache_isolation(tmp_path, monkeypatch):
    """修复 6：DataStep 参数解析与校验生效；QuoteRealtimeStep 缓存按 brain 会话隔离。"""
    from src.stock_analyzer.data_source.intraday_client import IntradayClient
    from src.wheel.config import WheelConfigError, load_wheel_config
    from src.wheel.data.steps import QuoteRealtimeStep
    from src.wheel.protocol import Context, PersonaPolicy

    # 1) DataStep 非法参数在配置加载时报错
    bad = tmp_path / "bad_data.yaml"
    bad.write_text(
        "wheels:\n  w1:\n    kind: decision\n"
        "    data: [quote_realtime(bad_param=1)]\n"
        "    propose: [rule.max_loss]\n"
        "    output: exit_proposal\n"
        "personas: {}\n",
    )
    with pytest.raises(WheelConfigError, match="bad_param"):
        load_wheel_config(bad)

    # 2) DataStep 合法内联参数可通过校验
    good = tmp_path / "good_data.yaml"
    good.write_text(
        "wheels:\n  w1:\n    kind: decision\n"
        '    data: ["position", "quote_realtime(period=5, bar_limit=3)"]\n'
        "    propose: [rule.max_loss]\n"
        "    output: exit_proposal\n"
        "personas: {}\n",
    )
    assert "w1" in load_wheel_config(good).wheels

    # 3) 两个不同 brain 实例在同一时间戳 _T 下互不污染行情缓存
    prices = iter([201.0, 195.0])

    class _Snap:
        def __init__(self, p):
            self.current_price = p
            self.is_mock = False

    monkeypatch.setattr(
        IntradayClient, "fetch_intraday_snapshot",
        lambda self, symbol, **kw: _Snap(next(prices)),
    )
    (tmp_path / "s1").mkdir(exist_ok=True)
    b1 = _MockBrain(tmp_path / "s1")
    (tmp_path / "s2").mkdir(exist_ok=True)
    b2 = _MockBrain(tmp_path / "s2")
    step = QuoteRealtimeStep()
    ctx1 = Context(
        event=_parent(), persona=PersonaPolicy(persona_id="u", kind="ultra"),
        now=_T, brain=b1, data={"position": _ULTRA_POS},
    )
    ctx2 = Context(
        event=_parent(), persona=PersonaPolicy(persona_id="u", kind="ultra"),
        now=_T, brain=b2, data={"position": _ULTRA_POS},
    )
    assert step.load(ctx1, {})["quote"]["price"] == 201.0
    assert step.load(ctx2, {})["quote"]["price"] == 195.0
    # 同一 brain 会话内再次读取命中缓存（不再触发第 3 次拉取）
    assert step.load(ctx1, {})["quote"]["price"] == 201.0


def test_phase1_exit_bindings_are_live():
    """Phase 1.8：四个出场转盘绑定均已切换为 live，Phase 3 入场转盘已注册。"""
    from src.wheel.config import get_config

    cfg = get_config(reload=True)
    for pid in ("short_term_hot_rotation_v1", "duan_yongping_v1", "warren_buffett_v1"):
        assert cfg.binding_for(pid, "clock.intraday_5m").mode == "live"
        assert cfg.binding_for(pid, "clock.intraday_30m").mode == "live"
        assert cfg.binding_for(pid, "clock.next_open").wheel == "order_fill_at_open"
        assert cfg.binding_for(pid, "clock.pending_5m").wheel == "pending_trigger"
    assert cfg.binding_for("ultra_short_momentum_v1", "clock.intraday_5m").mode == "live"
    assert cfg.binding_for("ultra_short_momentum_v1", "clock.1430").mode == "live"
    assert cfg.binding_for("ultra_short_momentum_v1", "clock.1435").chain == (
        "market_screen_ultra", "entry_confirm_ultra",
    )


def test_phase3_funnel_to_decision_chain_relays_via_wheel_candidates_and_entry_gateway(
    tmp_path, monkeypatch,
):
    """Phase 3.1：market_screen_ultra 写 wheel_candidates → entry_confirm_ultra 逐票评估 →
    EntryGateway 按 score 排序取 Top-1（第 2 名标记 rejected:ranked_out_of_top_n）。"""
    from src.wheel.config import get_config
    from src.wheel.data.steps import CandidateIntradayStep
    from src.wheel.persona import persona_policy
    from src.wheel.runner import run_binding

    brain = _MockBrain(tmp_path)
    cfg = get_config(reload=True)
    binding = cfg.binding_for("ultra_short_momentum_v1", "clock.1435")
    pol = persona_policy(cfg, "ultra_short_momentum_v1", binding)
    ev = WheelEvent(
        event_id="ev-1435", type="clock.1435", due_at=_T.replace(hour=14, minute=35),
        persona_id="ultra_short_momentum_v1", subject={}, source="test",
    )

    candidates_in = [
        {"symbol": "300001", "name": "候选A", "days_watched": 1, "watch_id": "W-1"},
        {"symbol": "300002", "name": "候选B", "days_watched": 1, "watch_id": "W-2"},
        {"symbol": "300003", "name": "超期C", "days_watched": 6, "watch_id": "W-3"},
    ]

    metrics_by_sym = {
        "300001": {
            "valid": True, "price": 20.0, "open": 19.5, "high": 20.1, "low": 19.0,
            "prev_close": 19.2, "day_range": 1.1, "close_pos": 0.90,
            "amplitude": 0.055, "turnover": 5e8, "change_pct": 4.17,
            "ret5": 0.08, "ret20": 0.15, "drawdown": 0.01, "avg_to_20": 4e8,
            "trade_date": _T.date().isoformat(),
        },
        "300002": {
            "valid": True, "price": 30.0, "open": 29.0, "high": 30.1, "low": 28.5,
            "prev_close": 28.0, "day_range": 1.6, "close_pos": 0.95,
            "amplitude": 0.06, "turnover": 8e8, "change_pct": 7.14,
            "ret5": 0.12, "ret20": 0.25, "drawdown": 0.01, "avg_to_20": 5e8,
            "trade_date": _T.date().isoformat(),
        },
    }

    def _mock_cand_load(self, ctx, params):
        cand = dict(ctx.data.get("candidate") or {})
        sym = cand["symbol"]
        m = metrics_by_sym[sym]
        return {
            "candidate": cand,
            "quote": {"price": m["price"], "is_mock": False, "symbol": sym},
            "intraday_metrics": m,
        }

    monkeypatch.setattr(CandidateIntradayStep, "load", _mock_cand_load)

    run_binding(
        cfg, binding, ev, pol, brain,
        preloaded={"candidates": candidates_in, "regime_snapshot": {"regime": "neutral", "up_ratio": 0.6}},
        now=_T.replace(hour=14, minute=35),
    )

    # 1. wheel_candidates 表：超期 300003 被剔除，保留 300001 与 300002
    saved_cands = brain.store.list_wheel_candidates("ev-1435", "ultra_short_momentum_v1")
    assert [c["symbol"] for c in saved_cands] == ["300001", "300002"]

    # 2. EntryGateway 排序裁决：300002 得分更高 → accepted，300001 → rejected:ranked_out_of_top_n
    rows = {
        r["subject_key"]: dict(r)
        for r in brain.store._conn().execute(
            "SELECT subject_key, action, gateway, executed FROM wheel_decisions WHERE event_id='ev-1435'"
        ).fetchall()
    }
    assert rows["300002"]["action"] == "BUY" and rows["300002"]["gateway"] == "accepted"
    assert rows["300001"]["action"] == "BUY" and rows["300001"]["gateway"] == "rejected:ranked_out_of_top_n"


def test_phase3_order_fill_at_open_gap_guard(tmp_path):
    """Phase 3.2：order_fill_at_open 校验今日行情、高开 >3%、低开破止损、涨跌停封死。"""
    from src.wheel.config import get_config
    from src.wheel.persona import persona_policy
    from src.wheel.runner import run_wheel

    brain = _MockBrain(tmp_path)
    cfg = get_config(reload=True)
    wheel = cfg.wheels["order_fill_at_open"]
    pol = persona_policy(
        cfg, "short_term_hot_rotation_v1",
        cfg.binding_for("short_term_hot_rotation_v1", "clock.next_open"),
    )
    sig = {
        "signal_id": "SIG-OPEN-1",
        "symbol": "002134",
        "name": "天津普林",
        "entry_price": 24.0,
        "stop_loss": 22.08,
        "target_price": 26.4,
        "position_pct": 0.04,
        "strategy": "主力吸筹",
        "strategy_id": "institutional_accumulation",
    }

    def _eval_open(idx: int, quote: dict):
        ev = WheelEvent(
            event_id=f"ev-open-{idx}", type="clock.next_open",
            due_at=_T.replace(hour=9, minute=32),
            persona_id="short_term_hot_rotation_v1",
            subject={"signal_id": "SIG-OPEN-1", "symbol": "002134"},
            source="test",
        )
        res = run_wheel(
            wheel, ev, pol, brain, mode="shadow",
            preloaded={"signal": sig, "quote": quote},
            now=_T.replace(hour=9, minute=32),
        )
        return res.decisions[0]

    # 1) 昨日残留行情 → veto.quote_fresh_today
    d_stale = _eval_open(1, {"price": 24.0, "is_mock": False, "trade_date": "2026-10-09"})
    assert d_stale.action == "HOLD" and d_stale.decided_by == "veto.quote_fresh_today"

    # 2) 高开 +5% (> +3%) → veto.open_gap_guard
    d_high = _eval_open(2, {"price": 25.20, "is_mock": False, "trade_date": "2026-10-12"})
    assert d_high.action == "HOLD" and d_high.decided_by == "veto.open_gap_guard"

    # 3) 大幅低开跌破止损 22.08 → veto.open_gap_guard
    d_low = _eval_open(3, {"price": 21.90, "is_mock": False, "trade_date": "2026-10-12"})
    assert d_low.action == "HOLD" and d_low.decided_by == "veto.open_gap_guard"

    # 4) 正常开盘 +1% (24.24) → BUY 提议通过
    d_ok = _eval_open(4, {"price": 24.24, "is_mock": False, "trade_date": "2026-10-12"})
    assert d_ok.action == "BUY" and d_ok.decided_by == "rule.fill_at_open"


def test_phase3_pending_trigger_and_persona_profile(tmp_path):
    """Phase 3.3 & 3.6：pending_trigger 条件单触发 + PersonaProfile as_of 记忆视图。"""
    from src.wheel.config import get_config
    from src.wheel.persona import assemble_persona_profile, persona_policy
    from src.wheel.runner import run_wheel

    brain = _MockBrain(tmp_path)
    cfg = get_config(reload=True)
    wheel = cfg.wheels["pending_trigger"]
    pol = persona_policy(
        cfg, "short_term_hot_rotation_v1",
        cfg.binding_for("short_term_hot_rotation_v1", "clock.pending_5m"),
    )
    sig = {
        "signal_id": "SIG-PB-1",
        "symbol": "301591",
        "entry_condition": "pullback",
        "entry_price": 44.50,
        "stop_loss": 40.94,
        "target_price": 50.0,
        "position_pct": 0.04,
        "persona_id": "short_term_hot_rotation_v1",
    }
    ev = WheelEvent(
        event_id="ev-pb-1", type="clock.pending_5m", due_at=_T,
        persona_id="short_term_hot_rotation_v1",
        subject={"signal_id": "SIG-PB-1", "symbol": "301591"},
        source="test",
    )
    res = run_wheel(
        wheel, ev, pol, brain, mode="shadow",
        preloaded={"signal": sig, "quote": {"price": 44.49, "is_mock": False, "symbol": "301591"}},
        now=_T,
    )
    assert res.decisions[0].action == "BUY"

    # PersonaProfile 装配与 as_of 截取校验
    _open(brain, {
        **_ULTRA_POS, "position_id": "POS-PAST", "persona_id": "duan_yongping_v1",
        "entry_date": "2026-10-01",
    })
    brain.store._conn().execute(
        "UPDATE positions SET created_at='2026-10-01T10:00:00' WHERE position_id='POS-PAST'"
    )
    _open(brain, {
        **_ULTRA_POS, "position_id": "POS-FUTURE", "symbol": "600519",
        "persona_id": "duan_yongping_v1", "entry_date": "2026-10-20",
    })
    brain.store._conn().execute(
        "UPDATE positions SET created_at='2026-10-20T10:00:00' WHERE position_id='POS-FUTURE'"
    )
    brain.store._conn().commit()

    profile = assemble_persona_profile(
        cfg, "duan_yongping_v1", None, brain, as_of=datetime(2026, 10, 12, 15, 0),
    )
    assert profile.policy.kind == "value"
    hist_ids = [p["position_id"] for p in profile.memory.trade_history(days=60)]
    assert "POS-PAST" in hist_ids
    assert "POS-FUTURE" not in hist_ids


def test_phase3_4_and_phase4_daily_screen_entry_and_account_risk(tmp_path):
    """Phase 3.4 & Phase 4：market_screen -> entry_confirm 链、shared_stock_flags 过滤、
    账户日亏否决与连亏熔断按角色配置生效。"""
    from src.wheel.config import get_config
    from src.wheel.persona import compute_account_risk_metrics, persona_policy
    from src.wheel.runner import run_binding

    brain = _MockBrain(tmp_path)
    cfg = get_config(reload=True)
    binding = cfg.binding_for("short_term_hot_rotation_v1", "clock.1535")
    pol = persona_policy(cfg, "short_term_hot_rotation_v1", binding)
    # short_term_hot_rotation_v1 在 trading_persona.yaml 中配置 consecutive_loss_limit=3
    assert pol.consecutive_loss_limit == 3
    assert pol.max_daily_loss_pct == pytest.approx(0.03)

    # 标记 000002 存在退市风险（跨角色共享标记）
    # （created_at 回填到 _T 之前，绕过回放时钟 as_of 过滤）
    brain.store.set_shared_stock_flag("000002", "delisting_risk", "退市整理期")
    brain.store._conn().execute(
        "UPDATE shared_stock_flags SET created_at = ? WHERE symbol = '000002'",
        ("2026-10-11T09:00:00",),
    )

    cands = [
        {"symbol": "000001", "name": "平安银行", "qlib_score": 0.9, "kline_summary": {"current_price": 11.2}},
        {"symbol": "000002", "name": "万科A", "qlib_score": 0.8, "kline_summary": {"current_price": 7.5}},
    ]
    sigs = {
        "000001": {
            "signal_id": "SIG-D-1", "symbol": "000001", "name": "平安银行",
            "action": "BUY", "entry_price": 11.2, "stop_loss": 10.5, "target_price": 12.5,
            "position_pct": 0.05, "confidence": 0.85, "strategy": "主力吸筹",
            "strategy_id": "institutional_accumulation",
        },
    }
    ev = WheelEvent(
        event_id="ev-1535-ok", type="clock.1535", due_at=_T.replace(hour=15, minute=35),
        persona_id="short_term_hot_rotation_v1", subject={}, source="test",
    )
    run_binding(
        cfg, binding, ev, pol, brain,
        preloaded={
            "candidates": cands,
            "regime_snapshot": {"regime": "neutral", "recommended_posture": "selective_attack"},
            "signals_by_symbol": sigs,
        },
        now=_T.replace(hour=15, minute=35),
    )
    # 000002 被 shared_stock_flags 过滤，只有 000001 进入 wheel_candidates 并产出 BUY
    saved = brain.store.list_wheel_candidates("ev-1535-ok", "short_term_hot_rotation_v1")
    assert [c["symbol"] for c in saved] == ["000001"]
    dec = brain.store._conn().execute(
        "SELECT action, gateway FROM wheel_decisions WHERE event_id='ev-1535-ok' AND subject_key='000001'"
    ).fetchone()
    assert dec["action"] == "BUY" and dec["gateway"] == "accepted"

    # 制造 3 笔连亏 → 触发 short_term 的 consecutive_loss_limit=3，mod.consecutive_loss_throttle 将 BUY 调弱为 HOLD
    for i in range(3):
        pid = f"POS-LOSS-{i}"
        _open(brain, {
            **_ULTRA_POS, "position_id": pid, "symbol": f"30010{i}",
            "persona_id": "short_term_hot_rotation_v1",
        })
        brain.store.close_position(
            pid, 195.0, -500.0, closed_at=(_T - timedelta(days=i + 1)).isoformat(),
        )

    risk = compute_account_risk_metrics(
        brain, "short_term_hot_rotation_v1", now=_T.replace(hour=15, minute=35), emit_events=True,
    )
    assert risk["consecutive_losses"] == 3 and risk["consecutive_loss_breached"] is True

    ev2 = WheelEvent(
        event_id="ev-1535-throttled", type="clock.1535", due_at=_T.replace(hour=15, minute=35),
        persona_id="short_term_hot_rotation_v1", subject={}, source="test",
    )
    run_binding(
        cfg, binding, ev2, pol, brain,
        preloaded={
            "candidates": cands,
            "regime_snapshot": {"regime": "neutral", "recommended_posture": "selective_attack"},
            "signals_by_symbol": sigs,
        },
        now=_T.replace(hour=15, minute=35),
    )
    dec2 = brain.store._conn().execute(
        "SELECT action, decided_by FROM wheel_decisions WHERE event_id='ev-1535-throttled' AND subject_key='000001'"
    ).fetchone()
    assert dec2["action"] == "HOLD" and dec2["decided_by"] == "mod.consecutive_loss_throttle"


def test_phase3_7_experience_layer_per_persona_isolation(tmp_path):
    """Phase 3.7：短线角色的连续止损冷却不得污染价值角色。"""
    from src.experience_layer.stock_memory import StockMemory
    from src.experience_layer.strategy_ledger import StrategyLedger

    brain = _MockBrain(tmp_path)
    conn = brain.store._conn()
    today_iso = datetime.now().isoformat()
    for i in (1, 2):
        pid = f"POS-ST-{i}"
        _open(brain, {
            **_ULTRA_POS, "position_id": pid, "symbol": "600887",
            "persona_id": "short_term_hot_rotation_v1",
        })
        conn.execute(
            """INSERT INTO trade_attributions
            (attribution_id, position_id, symbol, name, entry_price, close_price,
             realized_pnl, pnl_pct, holding_days, outcome, primary_cause,
             entry_regime, strategy_id, persona_id, created_at)
            VALUES (?, ?, '600887', '伊利股份', 28.0, 26.5, -1500, -5.3, 2, 'loss',
                    'stop_loss_triggered', 'rebound', 'value_investing',
                    'short_term_hot_rotation_v1', ?)""",
            (f"ATR-ST-{i}", pid, today_iso),
        )
    conn.commit()

    sm = StockMemory()
    sm.brain = brain
    assert sm.is_in_cooldown("600887", persona_id="short_term_hot_rotation_v1") is True
    assert sm.is_in_cooldown("600887", persona_id="duan_yongping_v1") is False

    sl = StrategyLedger()
    sl.brain = brain
    assert sl.get_stats("value_investing", regime="rebound", persona_id="duan_yongping_v1") is None
    st_stats = sl.get_stats("value_investing", regime="rebound", persona_id="short_term_hot_rotation_v1")
    assert st_stats is not None and st_stats["total_trades"] == 2


def test_phase4b_proposals_and_phase5_replay_benchmark(tmp_path):
    """Phase 4b 提议流转（样本不足降级 observe）与 Phase 5 WheelReplay + 随机买点对照闸门。"""
    from src.wheel.proposals import submit_proposal
    from src.wheel.replay import ReplayStep, WheelReplay, random_entry_benchmark

    brain = _MockBrain(tmp_path)
    p_obs = submit_proposal(
        brain.store,
        persona_id="short_term_hot_rotation_v1",
        kind="param_change",
        change={"stop_loss_pct": 0.04},
        rationale="小样本测试",
        sample_size=8,
    )
    assert p_obs["status"] == "observe"

    p_ok = submit_proposal(
        brain.store,
        persona_id="short_term_hot_rotation_v1",
        kind="param_change",
        change={"stop_loss_pct": 0.04},
        rationale="足量样本提议",
        sample_size=25,
    )
    assert p_ok["status"] == "proposed"
    assert brain.store.review_persona_proposal(p_ok["proposal_id"], "approved", reviewer="neo")
    rows = brain.store.list_persona_proposals(status="approved")
    assert len(rows) == 1 and rows[0]["proposal_id"] == p_ok["proposal_id"]

    # Phase 5: WheelReplay 确定性回放 + 随机买点对照闸门
    _open(brain, _ULTRA_POS)
    replayer = WheelReplay(brain)
    rep = replayer.replay(
        "ultra_short_momentum_v1",
        [
            ReplayStep(
                now=_T,
                event_type="clock.intraday_5m",
                subject={"position_id": "POS-U", "symbol": "688008"},
                preloaded={"quote": {"price": 194.0, "is_mock": False}},
            ),
        ],
        mode="shadow",
    )
    assert rep.steps_run == 1
    assert rep.decisions[0].action == "EXIT"

    bench_pass = random_entry_benchmark(
        [3.5, 4.2, 2.8, 5.1, 1.9],
        [-2.0, -1.0, 0.0, 0.5, 1.0, -0.5, 0.2],
    )
    assert bench_pass["passed"] is True and bench_pass["edge_pct"] > 2.0


def test_consecutive_loss_cooldown_releases_after_cold_period(tmp_path):
    """连亏达到上限后，最近一笔亏损过 cooldown 个交易日冷静期即恢复，不再锁死 14 天。"""
    from src.wheel.persona import compute_account_risk_metrics

    brain = _MockBrain(tmp_path)
    pid = "ultra_short_momentum_v1"
    for i, closed_at in enumerate(("2026-10-23T14:30:00", "2026-10-26T09:35:00")):
        pos_id = f"POS-L{i}"
        _open(brain, {**_ULTRA_POS, "position_id": pos_id, "symbol": f"30011{i}"})
        brain.store.close_position(pos_id, 194.0, -500.0, closed_at=closed_at)

    # 10-27：最近一笔亏损(10-26)距今 1 个交易日，仍在冷静期 → 拦截
    m1 = compute_account_risk_metrics(
        brain, pid, now=datetime(2026, 10, 27, 14, 35),
        consecutive_loss_limit=2, cooldown_days=1,
    )
    assert m1["consecutive_losses"] == 2
    assert m1["consecutive_loss_breached"] is True

    # 10-28：最近一笔亏损距今 2 个交易日 > cooldown 1 → 恢复交易（连亏计数仍保留）
    m2 = compute_account_risk_metrics(
        brain, pid, now=datetime(2026, 10, 28, 14, 35),
        consecutive_loss_limit=2, cooldown_days=1,
    )
    assert m2["consecutive_losses"] == 2
    assert m2["consecutive_loss_breached"] is False


def test_consecutive_loss_throttle_factor_respects_cooldown(tmp_path):
    """mod.consecutive_loss_throttle 与执行器一致读 consecutive_loss_breached，
    冷静期过后不再拦截买入。"""
    from types import SimpleNamespace

    from src.wheel.factors.entry_factors import ConsecutiveLossThrottleModifier
    from src.wheel.persona import compute_account_risk_metrics

    brain = _MockBrain(tmp_path)
    pid = "ultra_short_momentum_v1"
    for i, closed_at in enumerate(("2026-10-23T14:30:00", "2026-10-26T09:35:00")):
        pos_id = f"POS-T{i}"
        _open(brain, {**_ULTRA_POS, "position_id": pos_id, "symbol": f"30011{i}"})
        brain.store.close_position(pos_id, 194.0, -500.0, closed_at=closed_at)

    factor = ConsecutiveLossThrottleModifier()

    def _ctx(now):
        risk = compute_account_risk_metrics(
            brain, pid, now=now, consecutive_loss_limit=2, cooldown_days=1,
        )
        return SimpleNamespace(
            resolved=SimpleNamespace(action="BUY"),
            data={"account_risk": risk},
        )

    # 冷静期内（10-27）：拦截 BUY → HOLD
    op = factor.evaluate(_ctx(datetime(2026, 10, 27, 14, 35)), {})
    assert op.verdict == "modify" and op.action == "HOLD"

    # 冷静期过后（10-28）：放行 BUY
    op = factor.evaluate(_ctx(datetime(2026, 10, 28, 14, 35)), {})
    assert op.verdict == "pass"


def test_stale_position_reduce_cooldown(tmp_path):
    """价值持仓超 90 天减仓一次后，30 天内不再因超期规则重复减仓（防跨日切香肠）。"""
    from src.agents.reviewer.position_reviewer import PositionReviewAgent

    brain = _MockBrain(tmp_path)
    agent = PositionReviewAgent.__new__(PositionReviewAgent)
    agent.brain = brain

    pos = {
        "position_id": "POS-V", "symbol": "600660", "persona_id": "duan_yongping_v1",
        "entry_price": 55.0, "stop_loss": 44.0, "target_price": 75.0,
        "entry_date": "2026-07-25", "strategy_id": "value_investing",
        "current_qty": 400, "name": "福耀玻璃",
    }
    hold = {"action": "HOLD", "thesis_status": "intact"}

    # 建仓前的旧仓位平仓成交 → 不算本仓位已减仓，超期规则仍应触发 REDUCE
    brain.store.save_fill({
        "fill_id": "F-0", "order_id": "O-0", "persona_id": "duan_yongping_v1",
        "symbol": "600660", "side": "sell", "quantity": 400,
        "avg_price": 50.0, "filled_at": "2026-07-20T10:00:00",
    })
    out0 = agent._apply_rule_override(dict(hold), pos, current_price=53.0, pnl_pct=-3.6)
    assert out0["action"] == "REDUCE" and out0["risk_flag"] == "stale_position"

    # 30 天内无卖出 → 超期规则触发 REDUCE
    out1 = agent._apply_rule_override(dict(hold), pos, current_price=53.0, pnl_pct=-3.6)
    assert out1["action"] == "REDUCE" and out1["risk_flag"] == "stale_position"

    # 记录 30 天内的一笔实际卖出 → 超期冷却生效，保持 HOLD
    brain.store.save_fill({
        "fill_id": "F-1", "order_id": "O-1", "persona_id": "duan_yongping_v1",
        "symbol": "600660", "side": "sell", "quantity": 200,
        "avg_price": 52.9, "filled_at": datetime.now().isoformat(),
    })
    out2 = agent._apply_rule_override(dict(hold), pos, current_price=53.0, pnl_pct=-3.6)
    assert out2["action"] == "HOLD"

    # entry_date 下界：建仓前的旧仓位卖出不算"本仓位已减仓"
    recent = {
        **pos, "position_id": "POS-V2", "symbol": "603195",
        "entry_date": (datetime.now() - timedelta(days=2)).date().isoformat(),
    }
    brain.store.save_fill({
        "fill_id": "F-2", "order_id": "O-2", "persona_id": "duan_yongping_v1",
        "symbol": "603195", "side": "sell", "quantity": 400,
        "avg_price": 52.0,
        "filled_at": (datetime.now() - timedelta(days=3)).isoformat(),
    })
    assert agent._stale_reduce_on_cooldown(recent, cooldown_days=30) is False
    brain.store.save_fill({
        "fill_id": "F-3", "order_id": "O-3", "persona_id": "duan_yongping_v1",
        "symbol": "603195", "side": "sell", "quantity": 200,
        "avg_price": 53.0, "filled_at": datetime.now().isoformat(),
    })
    assert agent._stale_reduce_on_cooldown(recent, cooldown_days=30) is True


def test_value_pool_initial_screen_without_pe_in_snapshot(tmp_path):
    """快照只有市值无 PE 时，初筛只卡市值/非 ST，PE 上下限留待精筛（修 0 只入池 Bug）。"""
    from types import SimpleNamespace
    from unittest.mock import patch

    from src.agents.explorer import value_pool as vp

    def _stock(sym, mcap, pe=None, roe=None, st=False):
        return SimpleNamespace(
            symbol=sym, name=f"测试{sym}", market_cap_yi=mcap, pe_ttm=pe,
            roe=roe, is_st=st, turnover=5.0, pb=None, fcf_yield=None,
            debt_ratio=None, dividend_yield=None,
        )

    fake_snapshot = SimpleNamespace(stocks=[
        _stock("600519", 19000.0),   # 大市值，无 PE → 应过初筛
        _stock("600001", 50.0),      # 市值不足 → 初筛剔除
        _stock("600002", 300.0, st=True),  # ST → 剔除
        _stock("000001", 3000.0),    # 大市值 → 应过初筛
    ])

    class _FakeClient:
        def __init__(self, **kw): pass
        def fetch_snapshot(self): return fake_snapshot

    class _FakeFC:
        def enrich_quotes(self, candidates):
            for s in candidates:
                s.pe_ttm = 20.0
                s.roe = 15.0

    pool = vp.ValueStockPool.__new__(vp.ValueStockPool)  # 绕过 __init__，避免测试落盘 value_pool.db
    with patch("src.stock_analyzer.data_source.AkshareClient", _FakeClient), \
         patch("src.stock_analyzer.data_source.FundamentalClient", lambda *a, **k: _FakeFC()):
        result = pool._build_pool()

    syms = {s.symbol for s in result}
    assert syms == {"600519", "000001"}


def test_value_pool_mixed_pe_snapshot_keeps_missing_pe_rows(tmp_path):
    """快照部分有 PE 时，缺 PE 的大市值股不在初筛被误杀，留待 enrich 填充后精筛。"""
    from types import SimpleNamespace
    from unittest.mock import patch

    from src.agents.explorer import value_pool as vp

    def _stock(sym, mcap, pe=None, st=False):
        return SimpleNamespace(
            symbol=sym, name=f"测试{sym}", market_cap_yi=mcap, pe_ttm=pe,
            roe=None, is_st=st, turnover=5.0, pb=None, fcf_yield=None,
            debt_ratio=None, dividend_yield=None,
        )

    fake_snapshot = SimpleNamespace(stocks=[
        _stock("600519", 19000.0, pe=22.0),   # 有 PE 且合规 → 过初筛
        _stock("600660", 1500.0),             # 缺 PE 大市值 → 初筛放行，enrich 填充
        _stock("600661", 1200.0, pe=80.0),    # PE 超限 → 初筛剔除
        _stock("600662", 1300.0),             # 缺 PE 大市值 → enrich 后 PE 超标，精筛剔除
    ])

    class _FakeClient:
        def __init__(self, **kw): pass
        def fetch_snapshot(self): return fake_snapshot

    class _FakeFC:
        def enrich_quotes(self, candidates):
            for s in candidates:
                if s.pe_ttm is None:
                    s.pe_ttm = 70.0 if s.symbol == "600662" else 18.0
                s.roe = 15.0

    pool = vp.ValueStockPool.__new__(vp.ValueStockPool)
    with patch("src.stock_analyzer.data_source.AkshareClient", _FakeClient), \
         patch("src.stock_analyzer.data_source.FundamentalClient", lambda *a, **k: _FakeFC()):
        result = pool._build_pool()

    assert {s.symbol for s in result} == {"600519", "600660"}


def test_value_pool_stale_cache_missing_pe_rebuilds(tmp_path, monkeypatch):
    """7 天内缓存行全部缺 pe_ttm/fcf_yield 时视为旧版缓存，返回 None 触发重建。"""
    import sqlite3
    from datetime import date as date_cls

    from src.agents.explorer import value_pool as vp

    db = tmp_path / "value_pool.db"
    monkeypatch.setattr(vp, "_POOL_DB", db)
    pool = vp.ValueStockPool()
    conn = sqlite3.connect(str(db))
    today = date_cls.today().isoformat()
    conn.execute(
        "INSERT INTO value_pool (symbol, name, industry, pe_ttm, pb, roe, "
        "debt_to_equity, dividend_yield, fcf_yield, market_cap_yi, refresh_date) "
        "VALUES ('600519','茅台','白酒',NULL,NULL,30.0,NULL,NULL,NULL,19000.0,?)",
        (today,),
    )
    conn.commit()
    conn.close()
    assert pool._load_from_cache() is None


def test_check_stale_positions_skips_recently_reduced(tmp_path, monkeypatch):
    """14:50 超期检查与复审冷却共用同一窗口：已减仓的持仓不再注入强制复审。"""
    from src.agents.reviewer import stale_position_check as spc

    brain = _MockBrain(tmp_path)
    monkeypatch.setattr(spc, "get_central_brain", lambda: brain)

    pos = {
        **_ULTRA_POS, "position_id": "POS-V", "symbol": "600660",
        "persona_id": "duan_yongping_v1", "entry_price": 55.0,
        "entry_date": "2026-07-20", "strategy_id": "value_investing",
    }
    _open(brain, pos)

    stale = spc.check_stale_positions(persona_id="duan_yongping_v1")
    assert [p["position_id"] for p in stale] == ["POS-V"]

    # 30 天冷却窗内已实际减仓 → 不再重复强制复审
    brain.store.save_fill({
        "fill_id": "F-S1", "order_id": "O-S1", "persona_id": "duan_yongping_v1",
        "symbol": "600660", "side": "sell", "quantity": 200,
        "avg_price": 52.0, "filled_at": datetime.now().isoformat(),
    })
    assert spc.check_stale_positions(persona_id="duan_yongping_v1") == []


def _attr(brain, attr_id, pos_id, symbol, outcome, pnl=-3.0,
          strategy="s1", created_at=None):
    """写归因记录（persona 归属经 positions 表 fallback，不直接写 ta.persona_id）。"""
    brain.store.save_attribution({
        "attribution_id": attr_id, "position_id": pos_id, "symbol": symbol,
        "outcome": outcome, "primary_cause": "test", "pnl_pct": pnl,
        "strategy_id": strategy, "entry_regime": "bull",
        "created_at": created_at or datetime.now().isoformat(),
    })


def test_dynamic_blacklist_persona_isolation(tmp_path, monkeypatch):
    """DynamicBlacklist 的个股冷却/胜率降权只统计本角色的归因，不串其他角色。"""
    from src.experience_layer import dynamic_blacklist as db_mod
    from src.experience_layer import stock_memory as sm_mod
    from src.experience_layer import strategy_ledger as sl_mod

    brain = _MockBrain(tmp_path)
    for mod in (db_mod, sm_mod, sl_mod):
        monkeypatch.setattr(mod, "get_central_brain", lambda: brain)

    # persona A 在同一只票上连亏 2 笔 → 触发个股冷却
    for i in range(2):
        pid = f"POS-A{i}"
        _open(brain, {**_ULTRA_POS, "position_id": pid, "symbol": "300999",
                      "persona_id": "short_term_hot_rotation_v1",
                      "entry_date": "2026-10-10"})
        brain.store.close_position(pid, 190.0, -500.0,
                                   closed_at=f"2026-10-{20 + i}T14:00:00")
        _attr(brain, f"AT-A{i}", pid, "300999", "loss")

    bl_a = db_mod.DynamicBlacklist(persona_id="short_term_hot_rotation_v1")
    bl_b = db_mod.DynamicBlacklist(persona_id="duan_yongping_v1")

    blocked_a, _ = bl_a.is_blocked("300999")
    blocked_b, _ = bl_b.is_blocked("300999")
    assert blocked_a is True       # A 自己的连亏冷却
    assert blocked_b is False      # B 不受 A 的连亏影响（修复前会误拦）


def test_strategy_ledger_get_all_stats_persona_isolation(tmp_path, monkeypatch):
    """get_all_stats(persona_id=...) 只聚合该角色的归因行。"""
    from src.experience_layer import strategy_ledger as sl_mod

    brain = _MockBrain(tmp_path)
    monkeypatch.setattr(sl_mod, "get_central_brain", lambda: brain)

    for persona, n_losses in (("short_term_hot_rotation_v1", 6), ("duan_yongping_v1", 0)):
        for i in range(6):
            pid = f"POS-{persona[:4]}-{i}"
            _open(brain, {**_ULTRA_POS, "position_id": pid, "symbol": f"600{i}0",
                          "persona_id": persona, "entry_date": "2026-10-01"})
            outcome = "loss" if i < n_losses else "win"
            pnl = -3.0 if outcome == "loss" else 5.0
            brain.store.close_position(pid, 190.0, pnl * 100,
                                       closed_at=f"2026-10-{10 + i}T14:00:00")
            _attr(brain, f"AT-{persona[:4]}-{i}", pid, f"600{i}0", outcome,
                  pnl=pnl, strategy="hot_rotation")

    from src.experience_layer.strategy_ledger import StrategyLedger
    ledger = StrategyLedger()

    all_stats = ledger.get_all_stats()
    short_stats = ledger.get_all_stats(persona_id="short_term_hot_rotation_v1")
    duan_stats = ledger.get_all_stats(persona_id="duan_yongping_v1")

    s_short = next(s for s in short_stats if s["strategy_id"] == "hot_rotation")
    s_duan = next(s for s in duan_stats if s["strategy_id"] == "hot_rotation")
    s_all = next(s for s in all_stats if s["strategy_id"] == "hot_rotation")

    assert s_short["total_trades"] == 6 and s_short["losses"] == 6
    assert s_short["recommendation"] == "SUSPEND"          # 短线连亏 → 暂停
    assert s_duan["total_trades"] == 6 and s_duan["wins"] == 6
    assert s_duan["recommendation"] == "BOOST"             # 段永平全胜 → 加权
    assert s_all["total_trades"] == 12                     # 未隔离时合计
    # ExperienceAPI 门面透传 persona_id
    from src.experience_layer.experience_api import ExperienceAPI
    api = ExperienceAPI.__new__(ExperienceAPI)
    api.strategy_ledger = ledger
    api_stats = api.get_all_strategy_stats(persona_id="duan_yongping_v1")
    assert next(s for s in api_stats if s["strategy_id"] == "hot_rotation")["wins"] == 6


def test_account_monitor_step_uses_persona_cooldown(tmp_path):
    """AccountMonitorStep 把 PersonaPolicy 的 cooldown_days 传入指标计算。"""
    from types import SimpleNamespace

    from src.wheel.data.steps import AccountMonitorStep

    brain = _MockBrain(tmp_path)
    pid = "ultra_short_momentum_v1"
    for i in range(2):
        pos_id = f"POS-M{i}"
        _open(brain, {**_ULTRA_POS, "position_id": pos_id, "symbol": f"30022{i}"})
        brain.store.close_position(pos_id, 194.0, -500.0,
                                   closed_at="2026-10-23T14:00:00")

    persona = SimpleNamespace(
        persona_id=pid, max_daily_loss_pct=0.02,
        consecutive_loss_limit=2, consecutive_loss_cooldown_days=5,
    )
    ctx = SimpleNamespace(
        data={}, brain=brain, persona=persona,
        now=datetime(2026, 10, 28, 14, 35),
    )
    out = AccountMonitorStep().load(ctx, {})
    # 最近亏损(10-23)距今 3 个交易日：cooldown=5 → 仍拦截；cooldown=1 → 已恢复
    assert out["account_risk"]["consecutive_loss_breached"] is True

    persona.consecutive_loss_cooldown_days = 1
    out = AccountMonitorStep().load(ctx, {})
    assert out["account_risk"]["consecutive_loss_breached"] is False


def test_shared_flags_blacklist_factor_blocks_cooldown_symbol(tmp_path, monkeypatch):
    """screen.shared_flags_and_blacklist 真正过滤冷却标的（修 get_blacklisted_symbols 静默 AttributeError）。"""
    from types import SimpleNamespace

    from src.experience_layer import dynamic_blacklist as db_mod
    from src.experience_layer import stock_memory as sm_mod
    from src.experience_layer import strategy_ledger as sl_mod
    from src.wheel.factors.entry_factors import SharedFlagsAndBlacklistScreenFactor

    brain = _MockBrain(tmp_path)
    for mod in (db_mod, sm_mod, sl_mod):
        monkeypatch.setattr(mod, "get_central_brain", lambda: brain)

    pid = "short_term_hot_rotation_v1"
    for i in range(2):  # 连亏 2 笔 → 300999 进入个股冷却
        pos_id = f"POS-B{i}"
        _open(brain, {**_ULTRA_POS, "position_id": pos_id, "symbol": "300999",
                      "persona_id": pid, "entry_date": "2026-10-10"})
        brain.store.close_position(pos_id, 190.0, -500.0,
                                   closed_at=f"2026-10-{20 + i}T14:00:00")
        _attr(brain, f"AT-B{i}", pos_id, "300999", "loss",
              created_at=f"2026-10-{20 + i}T15:00:00")

    ctx = SimpleNamespace(
        brain=brain,
        persona=SimpleNamespace(persona_id=pid),
        data={"candidates": [{"symbol": "300999"}, {"symbol": "600660"}]},
        # 回放时钟：10-22 在最后一笔亏损(10-21)之后、冷却截止(10-26)之前
        now=datetime(2026, 10, 22),
    )
    op = SharedFlagsAndBlacklistScreenFactor().evaluate(ctx, {})
    kept_syms = {s["symbol"] for s in op.evidence["kept"]}
    assert kept_syms == {"600660"}                        # 冷却标的被拦截
    assert any("300999" in b for b in op.evidence["blocked"])

    # 换角色 persona：A 的冷却不影响 B
    ctx.persona = SimpleNamespace(persona_id="duan_yongping_v1")
    op = SharedFlagsAndBlacklistScreenFactor().evaluate(ctx, {})
    assert {s["symbol"] for s in op.evidence["kept"]} == {"300999", "600660"}


def test_attribution_lesson_persist_persona_id(tmp_path):
    """save_attribution/save_lesson 经 positions 关联自动落 persona_id。"""
    brain = _MockBrain(tmp_path)
    pid = "duan_yongping_v1"
    _open(brain, {**_ULTRA_POS, "position_id": "POS-P", "symbol": "600660",
                  "persona_id": pid, "entry_date": "2026-10-01"})
    brain.store.close_position("POS-P", 190.0, -500.0, closed_at="2026-10-20T14:00:00")

    brain.store.save_attribution({
        "attribution_id": "AT-P", "position_id": "POS-P", "symbol": "600660",
        "outcome": "loss", "primary_cause": "test", "strategy_id": "value_investing",
    })
    brain.store.save_lesson({
        "lesson_id": "LSN-P", "attribution_id": "AT-P", "symbol": "600660",
        "strategy_id": "value_investing", "outcome": "loss", "lesson_text": "测试教训",
    })

    conn = brain.store._conn()
    ta = conn.execute("SELECT persona_id FROM trade_attributions WHERE attribution_id='AT-P'").fetchone()
    ls = conn.execute("SELECT persona_id FROM lessons WHERE lesson_id='LSN-P'").fetchone()
    assert ta["persona_id"] == pid and ls["persona_id"] == pid

    # get_recent_lessons 角色隔离：B 角色看不到 A 的教训
    assert brain.store.get_recent_lessons(persona_id=pid, limit=5)
    assert brain.store.get_recent_lessons(persona_id="short_term_hot_rotation_v1", limit=5) == []


def test_llm_signal_propose_uses_raw_position_pct(tmp_path):
    """llm.signal_propose 只读 Strategist 原始 position_pct，不混入旧链路 approved_position_pct。"""
    from types import SimpleNamespace

    from src.wheel.factors.entry_factors import LlmSignalProposeFactor

    ctx = SimpleNamespace(
        data={
            "candidate": {"symbol": "300001", "name": "测试"},
            "quote": {"price": 10.0},
            "strategist_signal": {
                "action": "BUY", "symbol": "300001", "position_pct": 0.08,
                "approved_position_pct": 0.0,   # 旧链路 RiskGovernor 拒单置 0
            },
        },
    )
    op = LlmSignalProposeFactor().evaluate(ctx, {})
    assert op.verdict == "propose" and op.action == "BUY"
    assert op.qty_pct == 0.08   # 若误读 approved_position_pct 会得到 0.05 兜底值


def test_entry_gateway_lock_acquire_release(tmp_path):
    """EntryGateway 原子锁：签名与 MemoryStore.try_acquire_factor_lock / clear_factor_state 对齐。"""
    from src.wheel.entry_gateway import EntryGateway, LOCK_TTL

    gw = EntryGateway(_MockBrain(tmp_path))
    key = "duan_yongping_v1:600660"
    assert gw._acquire(key, _T) is True          # 首次抢锁成功
    assert gw._acquire(key, _T) is False         # 锁仍持有 → 拒
    gw._release(key)
    assert gw._acquire(key, _T) is True          # 释放后可再抢
    # TTL 过期后可重抢
    later = _T + LOCK_TTL + timedelta(seconds=1)
    gw._release(key)
    assert gw._acquire(key, later) is True


def test_portfolio_position_cap_runs_without_signal(tmp_path):
    """mod.portfolio_position_cap 不要求 signal 数据步（entry_confirm 中必须真正执行）。

    直接按 ctx.brain 持仓计算（不构造 RiskGovernor——其 __init__ 触全局 brain）。
    """
    from types import SimpleNamespace

    from src.wheel.factors.entry_factors import PortfolioPositionCapModifier

    assert PortfolioPositionCapModifier.requires == ()

    brain = _MockBrain(tmp_path)
    pid = "duan_yongping_v1"
    # 持仓市值 2x 资金 → used_pct 远超上限
    _open(brain, {**_ULTRA_POS, "position_id": "POS-BIG", "symbol": "600660",
                  "persona_id": pid, "entry_price": 1500.0, "current_qty": 400})
    ctx = SimpleNamespace(
        resolved=SimpleNamespace(action="BUY"),
        data={
            "strategist_signal": {"persona_id": pid},
            "regime_snapshot": {"regime": "neutral", "max_total_position_pct": 0.80},
        },
        brain=brain,
        persona=SimpleNamespace(persona_id=pid),
    )
    op = PortfolioPositionCapModifier().evaluate(ctx, {})
    assert op.verdict == "modify" and op.action == "HOLD"


def test_position_closed_event_dispatches_post_close(tmp_path):
    """close_position 发射 position.closed；记账类事件不受"已收盘/持仓已平"约束。"""
    from src.wheel.dispatcher import dispatch_once

    brain = _MockBrain(tmp_path)
    _open(brain, {**_ULTRA_POS, "position_id": "POS-C", "entry_date": "2026-10-10"})
    brain.store.close_position("POS-C", 190.0, -500.0, closed_at="2026-10-28T14:55:00")

    # 15:05 已收盘：position.* 记账事件仍应被分发（修复前会因"已收盘"/"持仓已平"被吞）
    stats = dispatch_once(brain, now=datetime(2026, 10, 28, 15, 5))
    assert stats["done"] >= 1
    row = brain.store._conn().execute(
        "SELECT status FROM wheel_events WHERE event_id = 'position.closed|POS-C'",
    ).fetchone()
    assert row["status"] == "done"
    # attribution 转盘留下决策留痕
    dec = brain.store._conn().execute(
        "SELECT action FROM wheel_decisions WHERE wheel = 'attribution' AND persona_id = ?",
        ("ultra_short_momentum_v1",),
    ).fetchone()
    assert dec is not None and dec["action"] == "WATCH"


def test_experience_layer_uses_injected_brain(tmp_path):
    """ExperienceAPI/DynamicBlacklist 接受注入 brain（转盘回测/测试不走全局单例）。"""
    from src.experience_layer.experience_api import ExperienceAPI

    brain = _MockBrain(tmp_path)
    pid = "short_term_hot_rotation_v1"
    for i in range(2):  # 连亏 2 笔 → 冷却
        pos_id = f"POS-I{i}"
        _open(brain, {**_ULTRA_POS, "position_id": pos_id, "symbol": "300999",
                      "persona_id": pid, "entry_date": "2026-10-10"})
        brain.store.close_position(pos_id, 190.0, -500.0,
                                   closed_at=f"2026-10-{20 + i}T14:00:00")
        _attr(brain, f"AT-I{i}", pos_id, "300999", "loss")

    exp = ExperienceAPI(brain=brain)
    assert exp.is_stock_in_cooldown("300999", persona_id=pid) is True
    bl = exp.blacklist_for(pid)
    assert "300999" in bl.get_blacklisted_symbols()


def test_wheel_entry_shadow_node_uses_raw_signals(tmp_path, monkeypatch):
    """clock.1535 图内节点：在风控删改前用 Strategist 原始信号跑入场转盘链。"""
    from src.agents.risk import risk_governor as rg_mod
    from src.graph import workflow as workflow_mod

    brain = _MockBrain(tmp_path)
    monkeypatch.setattr(workflow_mod, "get_central_brain", lambda: brain)
    monkeypatch.setattr(rg_mod, "get_central_brain", lambda: brain)

    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 28, 15, 35)   # 交易日 15:35

    monkeypatch.setattr(workflow_mod, "datetime", _FakeDT)

    state = {
        "persona_id": "short_term_hot_rotation_v1",
        "target_stocks": [{"symbol": "300001", "name": "测试", "price": 10.0,
                           "qlib_score": 0.8}],
        "market_regime": {"regime": "neutral", "recommended_posture": "selective_attack"},
        "trade_signals": [{
            "signal_id": "SIG-T1", "symbol": "300001", "action": "buy",
            "position_pct": 0.08, "approved_position_pct": 0.0,
            "entry_price": 10.0, "name": "测试",
        }],
    }
    workflow_mod.run_wheel_entry_shadow_node(state)

    conn = brain.store._conn()
    dec = conn.execute(
        "SELECT action, qty_pct FROM wheel_decisions WHERE wheel = 'entry_confirm'",
    ).fetchone()
    assert dec is not None
    assert dec["action"] == "BUY"
    assert abs(dec["qty_pct"] - 0.08) < 1e-6   # 原始 position_pct，非 approved(0.0)


# ── 五轮 review 修复回归 ─────────────────────────────────────


def test_run_funnel_chains_screen_results(tmp_path):
    """screen 链式过滤：前序步骤的清空结果必须传给后续 screen。

    修复前 run_funnel 只更新局部 candidates 不写回 ctx.data——多步漏斗只有
    最后一个 screen 生效，posture=exit / data_quality 硬闸被第二步复活。
    """
    from src.wheel.config import get_config
    from src.wheel.persona import persona_policy
    from src.wheel.runner import run_wheel

    cfg = get_config()
    brain = _MockBrain(tmp_path)
    pid = "short_term_hot_rotation_v1"
    binding = cfg.binding_for(pid, "clock.1535")
    pol = persona_policy(cfg, pid, binding)
    ev = WheelEvent(
        event_id="ev-funnel", type="clock.1535", due_at=_T, persona_id=pid,
    )
    res = run_wheel(
        cfg.wheels["market_screen"], ev, pol, brain, mode="shadow",
        preloaded={
            "candidates": [
                {"symbol": "300001", "name": "A"},
                {"symbol": "300002", "name": "B"},
            ],
            "regime_snapshot": {
                "regime": "panic", "recommended_posture": "exit",
                "data_quality": "unavailable",
            },
        },
        now=_T,
    )
    # posture=exit + data_quality 硬闸在第 1 步清空候选；
    # 修复前第 2 步 shared_flags 会从原始池复活全部 2 只
    assert res.candidates == []


def test_next_open_event_emitted_and_deferred(tmp_path, monkeypatch):
    """immediate 买单登记 clock.next_open 事件；live 绑定时旧路径让位。"""
    from dataclasses import replace
    from types import SimpleNamespace

    import src.wheel.config as wc
    from src.agents.executioner.executor import ExecutionEngine

    brain = _MockBrain(tmp_path)
    engine = ExecutionEngine("sess-no", persona_id="short_term_hot_rotation_v1",
                             brain=brain)
    sig = {"signal_id": "SIG-NO-1", "symbol": "300001", "action": "buy",
           "entry_price": 10.0}

    # shadow 绑定：mode 返回 "shadow"（事件登记推迟到成交确认后）
    assert engine._next_open_mode(sig) == "shadow"
    engine._emit_next_open_event(sig)
    row = brain.store._conn().execute(
        "SELECT type, due_at, status FROM wheel_events "
        "WHERE event_id = 'clock.next_open|SIG-NO-1'",
    ).fetchone()
    assert row["type"] == "clock.next_open" and row["status"] == "due"
    # 幂等：重复登记不报错、不重复行
    engine._emit_next_open_event(sig)
    cnt = brain.store._conn().execute(
        "SELECT COUNT(*) AS c FROM wheel_events "
        "WHERE event_id = 'clock.next_open|SIG-NO-1'",
    ).fetchone()
    assert cnt["c"] == 1

    # live 绑定：让位给次日开盘转盘
    real_cfg = wc.get_config()
    binding = real_cfg.binding_for(
        "short_term_hot_rotation_v1", "clock.next_open")
    live_binding = replace(binding, mode="live")
    monkeypatch.setattr(
        wc, "get_config",
        lambda: SimpleNamespace(
            binding_for=lambda pid, et: live_binding,
        ),
    )
    assert engine._next_open_mode(sig) == "live"

    # 转盘网关自身执行的信号不递归登记
    assert engine._next_open_mode({**sig, "_wheel_entry": True}) is None
    # 盘中条件单触发改写为 immediate 的信号不属于次日开盘语义
    assert engine._next_open_mode(
        {**sig, "market_price_at_trigger": 10.0},
    ) is None


def test_entry_gateway_shadow_same_signal_not_duplicate(tmp_path):
    """影子裁决：旧路径已按同一 signal_id 建的持仓不算 duplicate_position。"""
    from types import SimpleNamespace

    from src.wheel.entry_gateway import EntryGateway
    from src.wheel.protocol import Decision

    brain = _MockBrain(tmp_path)
    pid = "short_term_hot_rotation_v1"
    # 旧路径 15:35 已按 SIG-NO-1 建仓（positions.original_signal_id 列）
    pos = {**_ULTRA_POS, "position_id": "POS-DUP", "symbol": "300001",
           "persona_id": pid, "entry_date": "2026-10-11"}
    _open(brain, pos)
    brain.store._conn().execute(
        "UPDATE positions SET original_signal_id = 'SIG-NO-1' "
        "WHERE position_id = 'POS-DUP'"
    )
    # 另一持仓占用不同 signal → 仍算重复
    pos2 = {**_ULTRA_POS, "position_id": "POS-DUP2", "symbol": "300002",
            "persona_id": pid, "entry_date": "2026-10-11"}
    _open(brain, pos2)
    brain.store._conn().execute(
        "UPDATE positions SET original_signal_id = 'SIG-OTHER' "
        "WHERE position_id = 'POS-DUP2'"
    )

    ctx = SimpleNamespace(
        persona=SimpleNamespace(persona_id=pid, kind="short", params={}),
        data={"regime_snapshot": {"regime": "neutral", "data_quality": "real"}},
        mode="shadow",
        now=_T,
        event=SimpleNamespace(event_id="ev-gw", subject={}),
    )
    d_same = Decision(
        wheel="order_fill_at_open", action="BUY", subject_key="300001",
        qty_pct=0.05, price=10.0, reason="t",
        evidence={"signal_id": "SIG-NO-1", "symbol": "300001", "price": 10.0,
                  "position_pct": 0.05, "strategy_id": "s1"},
    )
    d_other = Decision(
        wheel="order_fill_at_open", action="BUY", subject_key="300002",
        qty_pct=0.05, price=10.0, reason="t",
        evidence={"signal_id": "SIG-NEW", "symbol": "300002", "price": 10.0,
                  "position_pct": 0.05, "strategy_id": "s1"},
    )
    out = EntryGateway(brain).submit_batch([d_same, d_other], ctx)
    assert out[d_same.subject_key]["accepted"] is True          # 同 signal 不算重复
    assert out[d_other.subject_key]["accepted"] is False        # 不同 signal 仍是重复
    assert out[d_other.subject_key]["reason"] == "duplicate_position"


def test_stock_memory_as_of_replay_clock(tmp_path):
    """StockMemory 冷却判定按 as_of 回放时钟，不吃未来归因、不用墙钟。"""
    from src.experience_layer.stock_memory import StockMemory

    brain = _MockBrain(tmp_path)
    pid = "short_term_hot_rotation_v1"
    # 2026-10-20 / 10-21 两笔连亏 → 冷却至 10-26
    for i, d in enumerate(("2026-10-20", "2026-10-21")):
        pos_id = f"POS-AO{i}"
        _open(brain, {**_ULTRA_POS, "position_id": pos_id, "symbol": "300999",
                      "persona_id": pid, "entry_date": "2026-10-15"})
        brain.store.close_position(pos_id, 190.0, -500.0,
                                   closed_at=f"{d}T14:00:00")
        _attr(brain, f"AT-AO{i}", pos_id, "300999", "loss",
              created_at=f"{d}T15:00:00")

    mem = StockMemory(brain=brain)
    # 回放 10-22：冷却中
    assert mem.is_in_cooldown(
        "300999", persona_id=pid, as_of=datetime(2026, 10, 22)) is True
    # 回放 10-19（归因尚未发生）：不冷却——未来归因不得泄露
    assert mem.is_in_cooldown(
        "300999", persona_id=pid, as_of=datetime(2026, 10, 19)) is False
    # 回放 10-28：冷却已过
    assert mem.is_in_cooldown(
        "300999", persona_id=pid, as_of=datetime(2026, 10, 28)) is False


def test_check_cold_period_as_of_replay_clock(tmp_path):
    """check_cold_period 按 as_of 判定（平仓快照与 regime 均不读未来数据）。"""
    from src.agents.reviewer.tail_hunt import check_cold_period

    brain = _MockBrain(tmp_path)
    pid = "ultra_short_momentum_v1"
    # 10-19（周日）止损平仓 -5% → 10-20 应冷静，10-22 起解除
    _open(brain, {**_ULTRA_POS, "position_id": "POS-CP", "symbol": "300888",
                  "persona_id": pid, "entry_date": "2026-10-16"})
    brain.store.close_position("POS-CP", 190.0, -500.0,
                               closed_at="2026-10-19T14:55:00")

    cold_20, _ = check_cold_period(brain, persona_id=pid,
                                   as_of=datetime(2026, 10, 20))
    assert cold_20 is True
    cold_22, _ = check_cold_period(brain, persona_id=pid,
                                   as_of=datetime(2026, 10, 22))
    assert cold_22 is False
    # 回放 10-17（平仓尚未发生）：不得看到未来的平仓记录
    cold_17, _ = check_cold_period(brain, persona_id=pid,
                                   as_of=datetime(2026, 10, 17))
    assert cold_17 is False


def test_kelly_sizer_persona_isolation(tmp_path):
    """KellySizer 按 persona_id 过滤归因，不跨角色混算胜率。"""
    from src.agents.strategist.kelly_sizer import KellySizer

    brain = _MockBrain(tmp_path)
    # 角色 A：8 笔全赢；角色 B：8 笔全输——同策略
    for i in range(8):
        pid = f"POS-KA{i}"
        _open(brain, {**_ULTRA_POS, "position_id": pid,
                      "persona_id": "duan_yongping_v1",
                      "symbol": f"60001{i}", "entry_date": "2026-09-01"})
        _attr(brain, f"AT-KA{i}", pid, f"60001{i}", "win", pnl=10.0,
              strategy="value_investing")
        pid2 = f"POS-KB{i}"
        _open(brain, {**_ULTRA_POS, "position_id": pid2,
                      "persona_id": "short_term_hot_rotation_v1",
                      "symbol": f"30002{i}", "entry_date": "2026-09-01"})
        _attr(brain, f"AT-KB{i}", pid2, f"30002{i}", "loss", pnl=-4.0,
              strategy="value_investing")

    sizer = KellySizer(brain=brain)
    ra = sizer.calculate("value_investing", persona_id="duan_yongping_v1")
    rb = sizer.calculate("value_investing",
                         persona_id="short_term_hot_rotation_v1")
    assert ra.stats["win_rate"] == 1.0
    assert rb.stats["win_rate"] == 0.0




def test_account_risk_metrics_as_of_no_future_leak(tmp_path):
    """compute_account_risk_metrics：now 之后的平仓不污染历史回放。

    修复前：未来连亏满足 closed_at >= window_start，且 trading_days_between
    (未来, 过去) 返回 0 <= cooldown → 历史日期 100% 误判为冷静期。
    """
    from src.wheel.persona import compute_account_risk_metrics

    brain = _MockBrain(tmp_path)
    pid = "short_term_hot_rotation_v1"
    # 10-20 / 10-21 两笔连亏
    for i, d in enumerate(("2026-10-20", "2026-10-21")):
        pos_id = f"POS-RK{i}"
        _open(brain, {**_ULTRA_POS, "position_id": pos_id,
                      "persona_id": pid, "entry_date": "2026-10-19"})
        brain.store.close_position(
            pos_id, 190.0, -500.0, closed_at=f"{d}T14:00:00")

    # 回放 10-15（连亏尚未发生）：不得触发冷静期
    m_past = compute_account_risk_metrics(
        brain, pid, now=datetime(2026, 10, 15, 9, 35),
        consecutive_loss_limit=2, cooldown_days=1,
    )
    assert m_past["consecutive_losses"] == 0
    assert m_past["consecutive_loss_breached"] is False

    # 回放 10-22（连亏之后 1 个交易日）：触发冷静期
    m_now = compute_account_risk_metrics(
        brain, pid, now=datetime(2026, 10, 22, 9, 35),
        consecutive_loss_limit=2, cooldown_days=1,
    )
    assert m_now["consecutive_losses"] == 2
    assert m_now["consecutive_loss_breached"] is True


def test_strategy_ledger_as_of_replay_clock(tmp_path):
    """StrategyLedger.get_stats/get_all_stats 按 as_of 截断，不吃未来归因。"""
    from src.experience_layer.strategy_ledger import StrategyLedger

    brain = _MockBrain(tmp_path)
    pid = "short_term_hot_rotation_v1"
    _open(brain, {**_ULTRA_POS, "position_id": "POS-SL1",
                  "persona_id": pid, "entry_date": "2026-10-01"})
    _attr(brain, "AT-SL1", "POS-SL1", "300001", "loss",
          strategy="institutional_accumulation",
          created_at="2026-10-20T15:00:00")

    ledger = StrategyLedger(brain=brain)
    # 回放 10-10：归因尚未发生 → 无统计
    assert ledger.get_stats(
        "institutional_accumulation", persona_id=pid,
        as_of=datetime(2026, 10, 10),
    ) is None
    assert ledger.get_all_stats(
        persona_id=pid, as_of=datetime(2026, 10, 10),
    ) == []
    # 回放 10-22：归因可见
    st = ledger.get_stats(
        "institutional_accumulation", persona_id=pid,
        as_of=datetime(2026, 10, 22),
    )
    assert st and st["total_trades"] == 1


def test_shared_flags_screen_as_of_replay_clock(tmp_path):
    """screen.shared_flags_and_blacklist：ctx.now 之后才打上的标记不拦截。"""
    from types import SimpleNamespace

    from src.wheel.factors.entry_factors import SharedFlagsAndBlacklistScreenFactor

    brain = _MockBrain(tmp_path)
    brain.store.set_shared_stock_flag("300001", "delisting_risk", "退市风险")

    factor = SharedFlagsAndBlacklistScreenFactor()
    persona = SimpleNamespace(persona_id="short_term_hot_rotation_v1")
    cands = [{"symbol": "300001"}]

    # 回放历史时刻（标记尚未打上）：不拦截——未来标记不泄露
    ctx_past = SimpleNamespace(
        persona=persona, brain=brain,
        data={"candidates": list(cands)},
        now=datetime(2020, 1, 1, 9, 35),
    )
    op_past = factor.evaluate(ctx_past, {})
    assert op_past.evidence["kept"] == cands

    # 标记已存在的时刻：正常拦截
    ctx_now = SimpleNamespace(
        persona=persona, brain=brain,
        data={"candidates": list(cands)},
        now=datetime(2099, 1, 1, 9, 35),
    )
    op_now = factor.evaluate(ctx_now, {})
    assert op_now.evidence["kept"] == []
    assert op_now.evidence["blocked"] == ["300001:delisting_risk"]


def test_entry_gateway_live_pending_condition_deferred(tmp_path):
    """live 模式 breakout 条件单挂起（pending_condition），不按市价成交也不判拒。"""
    from types import SimpleNamespace

    from src.wheel.entry_gateway import EntryGateway
    from src.wheel.protocol import Decision

    brain = _MockBrain(tmp_path)
    pid = "short_term_hot_rotation_v1"
    ctx = SimpleNamespace(
        persona=SimpleNamespace(persona_id=pid, kind="short", params={}),
        data={"regime_snapshot": {"regime": "neutral", "data_quality": "real"}},
        mode="live",
        now=_T,
        event=SimpleNamespace(event_id="ev-pc", subject={}),
    )
    d = Decision(
        wheel="entry_confirm", action="BUY", subject_key="300001",
        qty_pct=0.05, price=10.0, reason="t",
        evidence={"signal_id": "SIG-PC", "symbol": "300001", "price": 10.0,
                  "entry_condition": "breakout", "strategy_id": "s1"},
    )
    out = EntryGateway(brain).submit_batch([d], ctx)
    o = out[d.subject_key]
    assert o["accepted"] is True
    assert o["executed"] is False
    assert o["reason"] == "pending_condition"


def test_wheel_entry_shadow_node_live_suppresses_old_path_buys(tmp_path, monkeypatch):
    """clock.1535 为 live 时，节点从 trade_signals 移除买单阻止旧路径双执行。"""
    from dataclasses import replace
    from types import SimpleNamespace

    import src.wheel.config as wc
    import src.wheel.runner as wr
    import src.graph.workflow as wf

    real_cfg = wc.get_config()
    binding = real_cfg.binding_for(
        "short_term_hot_rotation_v1", "clock.1535")
    live_binding = replace(binding, mode="live")
    monkeypatch.setattr(
        wc, "get_config",
        lambda: SimpleNamespace(
            binding_for=lambda pid, et: live_binding,
            personas=real_cfg.personas, wheels=real_cfg.wheels,
        ),
    )
    # 不真跑转盘（live 会进 EntryGateway 执行），只验证买单抑制返回值
    monkeypatch.setattr(wr, "run_binding", lambda *a, **k: [])

    state = {
        "persona_id": "short_term_hot_rotation_v1",
        "trade_signals": [
            {"symbol": "300001", "action": "buy", "signal_id": "S1"},
            {"symbol": "300002", "action": "sell", "signal_id": "S2"},
        ],
        "target_stocks": [{"symbol": "300001"}],
        "market_regime": {"regime": "neutral", "data_quality": "real"},
    }
    out = wf.run_wheel_entry_shadow_node(state)
    assert out is not None
    assert [s["action"] for s in out["trade_signals"]] == ["sell"]


# ── 七轮 review 回归 ─────────────────────────────────────────


def test_pending_signals_scoped_by_persona(tmp_path):
    """list_pending_signals 按 persona 过滤，避免多角色循环重复扫描全表。"""
    brain = _MockBrain(tmp_path)
    now = datetime.now().isoformat()
    for sig_id, pid in [
        ("SIG-ST", "short_term_hot_rotation_v1"),
        ("SIG-UL", "ultra_short_momentum_v1"),
        ("SIG-OLD", None),
    ]:
        brain.store.save_trade_signal("s", {
            "signal_id": sig_id, "symbol": "300001", "action": "buy",
            "entry_price": 10.0, "entry_condition": "breakout",
            "timestamp": now, "persona_id": pid,
        })
    ultra = brain.store.list_pending_signals(persona_id="ultra_short_momentum_v1")
    assert [s["signal_id"] for s in ultra] == ["SIG-UL"]
    st = brain.store.list_pending_signals(
        persona_id="short_term_hot_rotation_v1", include_unassigned=True)
    assert sorted(s["signal_id"] for s in st) == ["SIG-OLD", "SIG-ST"]
    val = brain.store.list_pending_signals(persona_id="value_buy_and_hold_v1")
    assert val == []
    assert len(brain.store.list_pending_signals()) == 3


def test_regime_posture_gate_blocks_defend_observe(tmp_path):
    """screen.regime_posture_gate 对齐 RiskGovernor: defend/observe/bear 强制降级均禁开仓。"""
    from types import SimpleNamespace

    from src.wheel.factors.entry_factors import RegimePostureScreenFactor

    factor = RegimePostureScreenFactor()
    cands = [{"symbol": "300001"}]

    def _kept(regime):
        ctx = SimpleNamespace(
            data={"candidates": list(cands), "regime_snapshot": regime},
            now=_T,
        )
        return factor.evaluate(ctx, {}).evidence["kept"]

    real = {"regime": "neutral", "data_quality": "real"}
    assert _kept({**real, "recommended_posture": "defend"}) == []
    assert _kept({**real, "recommended_posture": "observe"}) == []
    assert _kept({**real, "recommended_posture": "exit"}) == []
    # bear/panic 下进攻姿态被强制降级为 defend/exit
    assert _kept({**real, "regime": "bear",
                  "recommended_posture": "attack"}) == []
    assert _kept({**real, "regime": "panic",
                  "recommended_posture": "selective_attack"}) == []
    # 正常进攻姿态放行
    assert _kept({**real, "recommended_posture": "attack"}) == cands


@pytest.mark.asyncio
async def test_hard_scan_live_reports_real_executed_flag(tmp_path, monkeypatch):
    """转盘 EXIT 决策被网关拦截时，hard_scan 不得虚报 executed=True。"""
    from src.agents.reviewer import hard_scan
    from src.wheel.protocol import Decision

    brain = _MockBrain(tmp_path)
    brain.store.open_position(
        position_id="POS-X", symbol="300001", entry_price=10.0,
        qty=100, entry_date="2026-10-08",
        persona_id="short_term_hot_rotation_v1", stop_loss=9.0,
    )
    decision = Decision(
        wheel="intraday_5m", subject_key="POS-X", action="EXIT",
        reason="硬止损", decision_id="WD-X",
    )
    monkeypatch.setattr(hard_scan, "_price_for", lambda *a, **k: (8.5, False))
    monkeypatch.setattr(hard_scan, "run_exit_shadow", lambda *a, **k: decision)
    monkeypatch.setattr(hard_scan, "wheel_exit_mode", lambda *a, **k: "live")

    async def _no_pending(*a, **kw):
        return []
    monkeypatch.setattr(
        "src.agents.reviewer.intraday_loop._check_pending_signals", _no_pending)
    monkeypatch.setattr(hard_scan, "expire_stale_signals", lambda b: 0)

    def _row(executed):
        brain.store._conn().execute(
            "INSERT OR REPLACE INTO wheel_decisions "
            "(decision_id, event_id, wheel, persona_id, subject_key, action, "
            " mode, executed, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("WD-X", "ev-x", "intraday_5m", "short_term_hot_rotation_v1",
             "POS-X", "EXIT", "live", executed, datetime.now().isoformat()),
        )
        brain.store._conn().commit()

    _row(0)  # 网关拒单（如 reduce_daily_cap）
    exits = await hard_scan.run_hard_scan(
        "short_term_hot_rotation_v1", force=True, brain=brain)
    assert exits[0]["action"] == "EXIT"
    assert exits[0]["executed"] is False
    assert exits[0]["executed_via"] == "wheel"

    _row(1)  # 网关真实成交
    exits = await hard_scan.run_hard_scan(
        "short_term_hot_rotation_v1", force=True, brain=brain)
    assert exits[0]["executed"] is True


def test_datastep_agents_accept_injected_brain(tmp_path):
    """ExplorerScanner/StrategistEngine/PositionReviewAgent 构造期可注入 brain。"""
    from src.agents.explorer.scanner import ExplorerScanner
    from src.agents.reviewer.position_reviewer import PositionReviewAgent
    from src.agents.strategist.signal_generator import StrategistEngine

    brain = _MockBrain(tmp_path)
    assert ExplorerScanner("s", brain=brain).brain is brain
    assert StrategistEngine("s", brain=brain).brain is brain
    assert PositionReviewAgent(brain=brain).brain is brain


def test_wheel_entry_shadow_node_live_defend_saves_watchlist(tmp_path, monkeypatch):
    """clock.1535 live + posture=defend：买单不进执行，但照旧存入自选池观察。"""
    from dataclasses import replace
    from types import SimpleNamespace

    import src.wheel.config as wc
    import src.wheel.runner as wr
    import src.graph.workflow as wf

    real_cfg = wc.get_config()
    binding = real_cfg.binding_for("short_term_hot_rotation_v1", "clock.1535")
    live_binding = replace(binding, mode="live")
    monkeypatch.setattr(
        wc, "get_config",
        lambda: SimpleNamespace(
            binding_for=lambda pid, et: live_binding,
            personas=real_cfg.personas, wheels=real_cfg.wheels,
        ),
    )
    monkeypatch.setattr(wr, "run_binding", lambda *a, **k: [])
    saved = []
    monkeypatch.setattr(
        wf, "_save_signals_to_watchlist",
        lambda sigs, state: saved.extend(sigs),
    )

    state = {
        "persona_id": "short_term_hot_rotation_v1",
        "trade_signals": [
            {"symbol": "300001", "action": "buy", "signal_id": "S1"},
            {"symbol": "300002", "action": "sell", "signal_id": "S2"},
        ],
        "target_stocks": [{"symbol": "300001"}],
        "market_regime": {
            "regime": "neutral", "data_quality": "real",
            "recommended_posture": "defend",
        },
    }
    out = wf.run_wheel_entry_shadow_node(state)
    assert [s["action"] for s in out["trade_signals"]] == ["sell"]
    assert [s["signal_id"] for s in saved] == ["S1"]
