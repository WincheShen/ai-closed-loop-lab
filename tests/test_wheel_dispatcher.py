"""Phase 2 事件层测试：Dispatcher 约束、后续事件链、市场事件广播、报告隔离。"""
from __future__ import annotations

from datetime import datetime, timedelta

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
    stats = dispatch_once(brain, now=_T + timedelta(minutes=1))
    assert stats["done"] == 1
    row = brain.store.list_wheel_events()[0]
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
    """market.regime_drift 广播 → 每个绑定角色对每个在持持仓记 REVIEW 决策。"""
    brain = _MockBrain(tmp_path)
    _open(brain)
    _open(brain, {**_ULTRA_POS, "position_id": "POS-S", "symbol": "000001",
                  "persona_id": "short_term_hot_rotation_v1"})
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
    # ultra 和 short_term 各自的持仓都被复审
    assert {"POS-U", "POS-S"} <= keys
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
    from src.wheel.data.steps import QuoteRealtimeStep
    QuoteRealtimeStep._cache["688008:202610120936"] = {
        "price": 201.0, "is_mock": False, "symbol": "688008",
    }
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
