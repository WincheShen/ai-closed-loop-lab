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
                    source="dispatcher", now=_T.replace(minute=39, second=30))
    run_exit_shadow(brain, p, price=194.0, is_mock=False, event_type="clock.intraday_5m",
                    source="hard_scan", now=_T.replace(minute=40))
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
                        source="hard_scan", now=_T.replace(minute=40, second=sec))
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
