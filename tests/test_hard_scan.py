"""硬止损 / 信号过期 — 不访问外网、不调 LLM。"""
from __future__ import annotations

from src.agents.reviewer.hard_rules import evaluate_hard_exit, position_pnl_pct
from src.agents.reviewer.hard_scan import expire_stale_signals
from src.agents.strategist.signal_generator import (
    _BANNED_STRATEGY_IDS,
    _infer_strategy_id,
    _normalize_strategy_name,
)
from src.infra.data_quality import (
    blocks_new_entries,
    classify_market_quality,
    strategy_blocked_by_quality,
)


def test_evaluate_hard_exit_stop_loss() -> None:
    pos = {"entry_price": 10.0, "stop_loss": 9.5, "target_price": 12.0}
    hit = evaluate_hard_exit(pos, 9.4)
    assert hit is not None
    assert hit["action"] == "EXIT"
    assert hit["risk_flag"] == "stop_loss_triggered"
    assert evaluate_hard_exit(pos, 9.8) is None


def test_evaluate_hard_exit_max_loss() -> None:
    pos = {"entry_price": 10.0, "stop_loss": 0, "target_price": 0}
    hit = evaluate_hard_exit(pos, 9.1)
    assert hit is not None
    assert hit["action"] == "EXIT"
    assert hit["risk_flag"] == "max_loss_exceeded"


def test_evaluate_hard_exit_target_reduce() -> None:
    pos = {"entry_price": 10.0, "stop_loss": 9.0, "target_price": 12.0}
    hit = evaluate_hard_exit(pos, 12.1)
    assert hit is not None
    assert hit["action"] == "REDUCE"


def test_short_side_pnl() -> None:
    pos = {"entry_price": 10.0, "side": "short", "stop_loss": 0}
    assert abs(position_pnl_pct(pos, 9.0) - 10.0) < 1e-6


def test_banned_strategy_variants() -> None:
    raw = "放量突破后第一次确认回踩"
    assert _infer_strategy_id(raw) == "volume_breakout"
    assert _infer_strategy_id(_normalize_strategy_name(raw)) in _BANNED_STRATEGY_IDS


def test_data_quality_gates() -> None:
    mock = classify_market_quality(
        is_mock=True, has_sectors=False, hot_sectors=[], ak_degraded=False,
    )
    assert mock["data_quality"] == "mock"
    assert blocks_new_entries({**mock, "is_mock_data": True})

    no_sec = classify_market_quality(
        is_mock=False, has_sectors=False, hot_sectors=[], ak_degraded=False,
    )
    assert no_sec["data_quality"] == "no_sector"
    assert not blocks_new_entries(no_sec)
    assert strategy_blocked_by_quality("hot_sector_pullback", no_sec)
    assert not strategy_blocked_by_quality("value_investing", no_sec)

    # 东财挂了但新浪真票：ak_degraded 不得把质量抬成 degraded
    sina = classify_market_quality(
        is_mock=False, has_sectors=False, hot_sectors=[], ak_degraded=True,
    )
    assert sina["data_quality"] == "no_sector"
    assert not blocks_new_entries(sina)
    assert not strategy_blocked_by_quality("value_investing", sina)

    mock_deg = classify_market_quality(
        is_mock=True, has_sectors=False, hot_sectors=[], ak_degraded=True,
    )
    assert mock_deg["data_quality"] == "degraded"
    assert blocks_new_entries(mock_deg)

    stale = classify_market_quality(
        is_mock=False, has_sectors=True, hot_sectors=["银行"], upstream_quality="stale",
    )
    assert stale["data_quality"] == "degraded"
    assert blocks_new_entries(stale)


class _FakeStore:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.updated: list[tuple[str, str]] = []

    def _conn(self):
        return self

    def execute(self, sql, params=None):
        status = "pending" if "pending" in sql else "active"
        days = 5 if "pending" in sql else 2
        # 简化：调用方分别查 pending / active，测试直接按 status 过滤
        self._last = [r for r in self.rows if r["status"] == status]
        return self

    def fetchall(self):
        return self._last

    def update_signal_status(self, signal_id: str, status: str) -> None:
        self.updated.append((signal_id, status))


class _FakeBrain:
    def __init__(self, store: _FakeStore) -> None:
        self.store = store


def test_expire_stale_signals() -> None:
    # expire_stale_signals 用 SQL datetime；这里只验证会调用 update。
    # 用真实 sqlite 内存库更稳。
    import sqlite3
    from types import SimpleNamespace

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE trade_signals (signal_id TEXT, symbol TEXT, status TEXT, timestamp TEXT)"
    )
    conn.execute(
        "INSERT INTO trade_signals VALUES ('A','000001','pending', datetime('now','-6 days'))"
    )
    conn.execute(
        "INSERT INTO trade_signals VALUES ('B','000002','active', datetime('now','-3 days'))"
    )
    conn.execute(
        "INSERT INTO trade_signals VALUES ('C','000003','pending', datetime('now','-1 days'))"
    )
    conn.commit()

    class Store:
        def _conn(self):
            return conn

        def update_signal_status(self, signal_id, status):
            conn.execute(
                "UPDATE trade_signals SET status=? WHERE signal_id=?",
                (status, signal_id),
            )
            conn.commit()

    n = expire_stale_signals(SimpleNamespace(store=Store()))
    assert n == 2
    left = {r["signal_id"]: r["status"] for r in conn.execute("SELECT * FROM trade_signals")}
    assert left["A"] == "expired"
    assert left["B"] == "expired"
    assert left["C"] == "pending"
