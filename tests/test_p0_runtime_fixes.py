"""P0 运行层修复回归：交易日历 / 强平禁用假价 / REDUCE 每日一次 / 价值持仓防噪音离场 / 隔夜客退出 LLM 复审。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from src.agents.reviewer.hard_rules import evaluate_hard_exit, is_value_position
from src.agents.reviewer.intraday_loop import _execute_review_action
from src.agents.reviewer.position_reviewer import PositionReviewAgent
from src.agents.reviewer.tail_hunt import execute_force_close_ultra_short
from src.central_brain.metadata_store import MemoryStore
from src.infra.trading_calendar import is_trading_day, previous_trading_day, trading_days_between
from src.stock_analyzer.data_source.intraday_client import IntradaySnapshot


class _DummyBus:
    def emit_order_fill(self, *args, **kwargs):
        pass


class _MockBrain:
    def __init__(self, tmp_path):
        self.store = MemoryStore(str(tmp_path / "p0.db"))
        self.bus = _DummyBus()

    def log_agent_event(self, *args, **kwargs):
        pass


def _snapshot(price: float, is_mock: bool = False) -> IntradaySnapshot:
    return IntradaySnapshot(
        symbol="002487", name="大金重工", current_price=price, prev_close=price,
        change_pct=0.0, high=price, low=price, open=price, volume=1, turnover=1,
        bars=[], is_mock=is_mock,
    )


# ── P0.1 交易日历 ─────────────────────────────────────────────

def test_calendar_national_day_and_mid_autumn_closed():
    assert not is_trading_day(date(2026, 9, 25))
    for d in range(1, 8):
        assert not is_trading_day(date(2026, 10, d))
    assert is_trading_day(date(2026, 9, 30))
    assert is_trading_day(date(2026, 10, 8))
    assert not is_trading_day(date(2026, 10, 10))  # 周六


def test_calendar_trading_days_between_skips_holidays():
    # 9-30 买入 → 10-08 是下一个交易日
    assert trading_days_between(date(2026, 9, 30), date(2026, 10, 8)) == 1
    assert previous_trading_day(date(2026, 10, 8)) == date(2026, 9, 30)


def test_scheduler_skips_on_holiday(monkeypatch):
    import scripts.scheduler as sched

    monkeypatch.setattr("src.infra.trading_calendar.date", type(
        "D", (date,), {"today": classmethod(lambda cls: date(2026, 10, 5))},
    ))
    calls = []

    @sched.skip_if_weekend
    def job():
        calls.append(1)

    job()
    assert calls == []


# ── P0.2 强平禁止假价 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_force_close_skips_without_real_quote(tmp_path, monkeypatch):
    brain = _MockBrain(tmp_path)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    brain.store.open_position(
        position_id="POS-FC-1", symbol="002487", name="大金重工",
        entry_price=46.29, qty=1200, entry_date=yesterday,
        persona_id="ultra_short_momentum_v1",
    )
    monkeypatch.setattr(
        "src.agents.reviewer.tail_hunt.IntradayClient.fetch_intraday_snapshot",
        lambda self, symbol, **kw: _snapshot(46.29, is_mock=True),
    )
    alerts = []
    monkeypatch.setattr("src.agents.reviewer.tail_hunt.notify_ops", lambda t, b, **kw: alerts.append(t))

    res = await execute_force_close_ultra_short(brain, "ultra_short_momentum_v1")

    assert res["closed"] == 0
    assert res["items"][0]["error"] == "no_real_quote"
    assert alerts, "无真实行情必须告警"
    assert len(brain.store.list_open_positions(persona_id="ultra_short_momentum_v1")) == 1


@pytest.mark.asyncio
async def test_review_action_rejects_missing_price(tmp_path):
    brain = _MockBrain(tmp_path)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    brain.store.open_position(
        position_id="POS-NP-1", symbol="600216", name="浙江医药",
        entry_price=13.76, qty=1000, entry_date=yesterday,
        persona_id="short_term_hot_rotation_v1",
    )
    pos = brain.store.get_position("POS-NP-1")
    res = await _execute_review_action(brain, "s", pos, {"action": "EXIT", "reason": "x"})
    assert res["executed"] is False
    assert brain.store.get_position("POS-NP-1")["status"] == "open"


# ── P0.3 REDUCE 每日一次 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_reduce_capped_once_per_day(tmp_path):
    brain = _MockBrain(tmp_path)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    brain.store.open_position(
        position_id="POS-RD-1", symbol="600216", name="浙江医药",
        entry_price=13.76, qty=1200, entry_date=yesterday,
        persona_id="short_term_hot_rotation_v1",
    )
    review = {"action": "REDUCE", "current_price": 13.98, "reason": "冲高回落"}

    first = await _execute_review_action(
        brain, "s1", brain.store.get_position("POS-RD-1"), dict(review),
        persona_id="short_term_hot_rotation_v1",
    )
    assert first["executed"] is True
    qty_after_first = brain.store.get_position("POS-RD-1")["current_qty"]
    assert qty_after_first == 600

    second = await _execute_review_action(
        brain, "s2", brain.store.get_position("POS-RD-1"), dict(review),
        persona_id="short_term_hot_rotation_v1",
    )
    assert second["executed"] is False
    assert brain.store.get_position("POS-RD-1")["current_qty"] == 600


# ── P0.3 价值持仓防噪音离场 ───────────────────────────────────

_VALUE_POS = {
    "position_id": "POS-V", "symbol": "600809", "persona_id": "warren_buffett_v1",
    "entry_price": 123.75, "stop_loss": 92.8, "target_price": 200.0,
    "entry_date": "2026-08-03", "strategy_id": "value_investing",
}
_SHORT_POS = {
    "position_id": "POS-S", "symbol": "301626", "persona_id": "short_term_hot_rotation_v1",
    "entry_price": 100.0, "stop_loss": 85.0, "target_price": 120.0,
    "entry_date": "2026-08-18", "strategy_id": "hot_sector_pullback",
}


def test_hard_rule_minus8_skips_value_but_hits_short():
    assert is_value_position(_VALUE_POS)
    assert not is_value_position(_SHORT_POS)
    assert evaluate_hard_exit(_VALUE_POS, 113.0) is None  # -8.7%，止损在 92.8
    assert evaluate_hard_exit(_SHORT_POS, 91.0)["action"] == "EXIT"  # -9%
    assert evaluate_hard_exit(_VALUE_POS, 92.0)["action"] == "EXIT"  # 跌破宽止损仍然出


def _reviewer() -> PositionReviewAgent:
    return object.__new__(PositionReviewAgent)


def test_value_llm_noise_exit_downgraded_to_hold():
    r = _reviewer()._apply_rule_override(
        {"action": "EXIT", "reason": "盘中放量下跌", "thesis_status": "weakened"},
        _VALUE_POS, 118.0, -4.6,
    )
    assert r["action"] == "HOLD"


def test_value_thesis_invalidated_exit_allowed():
    r = _reviewer()._apply_rule_override(
        {"action": "EXIT", "reason": "财务造假", "thesis_status": "invalidated"},
        _VALUE_POS, 118.0, -4.6,
    )
    assert r["action"] == "EXIT"


def test_minus8_override_only_for_short():
    v = _reviewer()._apply_rule_override({"action": "HOLD", "reason": ""}, _VALUE_POS, 113.0, -8.7)
    s = _reviewer()._apply_rule_override({"action": "HOLD", "reason": ""}, _SHORT_POS, 91.0, -9.0)
    assert v["action"] == "HOLD"
    assert s["action"] == "EXIT"


# ── P0.4 隔夜客退出 LLM 复审 ──────────────────────────────────

def test_ultra_short_persona_disallows_llm_review():
    from src.agents.cio.trading_persona import list_personas, persona_allows_llm_review

    ultra = next(p for p in list_personas() if p["id"] == "ultra_short_momentum_v1")
    assert ultra["intraday_llm_review"] is False
    assert persona_allows_llm_review("ultra_short_momentum_v1") is False
    assert persona_allows_llm_review("short_term_hot_rotation_v1") is True


def test_review_all_positions_skips_ultra_short(tmp_path):
    brain = _MockBrain(tmp_path)
    brain.store.open_position(
        position_id="POS-U-1", symbol="688008", name="澜起科技",
        entry_price=197.72, qty=300, entry_date="2026-10-08",
        persona_id="ultra_short_momentum_v1",
    )
    agent = object.__new__(PositionReviewAgent)
    agent.brain = brain
    agent.persona_id = "ultra_short_momentum_v1"
    called = []
    agent.review_position = lambda pos, **kw: called.append(pos) or {"action": "HOLD"}

    assert agent.review_all_positions() == []
    assert called == []
