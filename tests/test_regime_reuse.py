"""mock regime 沿用 TTL。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from src.agents.cio.market_brain import MarketBrain, MarketRegimeSnapshot
from src.infra.data_quality import (
    blocks_new_entries,
    regime_reuse_expired,
    trading_days_between,
)


def test_trading_days_skips_weekend() -> None:
    # Fri 8/21 → Sat/Sun → Mon 8/24 = 1 trading day
    assert trading_days_between(date(2026, 8, 21), date(2026, 8, 24)) == 1
    # Fri 8/21 → Tue 8/25 = 2
    assert trading_days_between(date(2026, 8, 21), date(2026, 8, 25)) == 2
    assert trading_days_between(date(2026, 8, 21), date(2026, 8, 21)) == 0


def test_reuse_ttl_two_trading_days() -> None:
    # 当天 / 下一交易日仍可沿用；超过 2 个交易日过期
    assert not regime_reuse_expired("2026-08-21", today=date(2026, 8, 21))
    assert not regime_reuse_expired("2026-08-21", today=date(2026, 8, 24))  # +1
    assert not regime_reuse_expired("2026-08-21", today=date(2026, 8, 25))  # +2
    assert regime_reuse_expired("2026-08-21", today=date(2026, 8, 26))      # +3
    assert regime_reuse_expired(None, today=date(2026, 8, 22))
    assert regime_reuse_expired("bad", today=date(2026, 8, 22))


def test_halt_snapshot_blocks_entries(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPS_ALERT_LOG", str(tmp_path / "ops.log"))
    monkeypatch.delenv("OPS_WEBHOOK_URL", raising=False)
    brain = MarketBrain.__new__(MarketBrain)
    brain.session_id = "s"
    brain.logger = MagicMock()
    brain.persona = MagicMock(persona_version="v")
    store = MagicMock()
    brain.brain = MagicMock(store=store)
    snap = MarketBrain._halt_for_stale_mock(brain, None)
    assert snap.recommended_posture == "defend"
    assert snap.data_quality == "unavailable"
    assert snap.max_total_position_pct == 0.0
    assert blocks_new_entries(snap.to_dict())
    store.save_market_regime.assert_called_once()


def test_halt_uses_prev_date_in_reason(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPS_ALERT_LOG", str(tmp_path / "ops.log"))
    monkeypatch.delenv("OPS_WEBHOOK_URL", raising=False)
    brain = MarketBrain.__new__(MarketBrain)
    brain.session_id = "s"
    brain.logger = MagicMock()
    brain.persona = MagicMock(persona_version="v")
    brain.brain = MagicMock(store=MagicMock())
    prev = MarketRegimeSnapshot(
        snapshot_id="old",
        trade_date="2026-08-01",
        regime="bull",
        risk_appetite="high",
        recommended_posture="attack",
        max_total_position_pct=0.5,
    )
    snap = MarketBrain._halt_for_stale_mock(brain, prev)
    assert "2026-08-01" in snap.summary
    assert snap.evidence["original_date"] == "2026-08-01"
