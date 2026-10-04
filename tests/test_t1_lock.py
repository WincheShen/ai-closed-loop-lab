"""测试 A 股 T+1 交易纪律校验与分时高可用防护。"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any
import pytest

from src.agents.executioner.executor import ExecutionEngine
from src.agents.reviewer.hard_scan import run_hard_scan
from src.agents.reviewer.position_reviewer import PositionReviewAgent
from src.stock_analyzer.data_source.intraday_client import IntradayClient, MinuteBar


class _MockBrain:
    def __init__(self, tmp_path):
        from src.central_brain.metadata_store import MetadataStore
        db_path = str(tmp_path / "central_brain.db")
        self.store = MetadataStore(db_path)
        self.bus = None

    def log_agent_event(self, **kwargs):
        pass


@pytest.mark.asyncio
async def test_executor_t1_lock_rejects_same_day_sell(tmp_path):
    """测试 executor 严格拦截当日买入标的的卖出信号。"""
    brain = _MockBrain(tmp_path)
    today = date.today().isoformat()

    # 1. 建立今日入场的持仓
    brain.store.open_position(
        position_id="POS-T0-1",
        symbol="600383",
        name="金地集团",
        entry_price=2.50,
        qty=1000,
        entry_date=today,
        persona_id="short_term_hot_rotation_v1",
    )

    engine = ExecutionEngine("test-session-1", persona_id="short_term_hot_rotation_v1", brain=brain)

    # 2. 尝试当日卖出
    sell_sig = {
        "signal_id": "SIG-SELL-1",
        "symbol": "600383",
        "action": "sell",
        "entry_price": 2.40,
        "target_qty": 1000,
        "strategy": "stop_loss",
    }
    brain.store.save_trade_signal("test-session-1", sell_sig)
    order, fill = await engine._mock_execute(sell_sig)

    assert order["status"] == "rejected"
    assert fill is None
    # 验证信号状态被标记为 rejected_t1_locked
    sig_row = brain.store._conn().execute(
        "SELECT status FROM trade_signals WHERE signal_id='SIG-SELL-1'"
    ).fetchone()
    assert sig_row["status"] == "rejected_t1_locked"

    # 持仓依然保持 open，未被平仓
    open_pos = brain.store.list_open_positions(persona_id="short_term_hot_rotation_v1")
    assert len(open_pos) == 1
    assert open_pos[0]["symbol"] == "600383"


@pytest.mark.asyncio
async def test_executor_t1_allows_overnight_sell(tmp_path):
    """测试 executor 允许卖出隔夜持仓 (entry_date < today)。"""
    brain = _MockBrain(tmp_path)
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # 1. 建立昨日入场的持仓
    brain.store.open_position(
        position_id="POS-T1-1",
        symbol="300207",
        name="欣旺达",
        entry_price=20.00,
        qty=1000,
        entry_date=yesterday,
        persona_id="ultra_short_momentum_v1",
    )

    # 初始化资金账户
    brain.store.create_account(
        persona_id="ultra_short_momentum_v1",
        persona_name="极速动量隔夜客",
        capital=300000.0,
    )

    engine = ExecutionEngine("test-session-2", persona_id="ultra_short_momentum_v1", brain=brain)

    sell_sig = {
        "signal_id": "SIG-SELL-2",
        "symbol": "300207",
        "action": "sell",
        "entry_price": 21.00,
        "target_qty": 1000,
        "strategy": "take_profit",
    }
    order, fill = await engine._mock_execute(sell_sig)

    assert order["status"] == "filled"
    assert fill is not None
    assert fill["quantity"] == 1000
    assert fill["avg_price"] == 21.00


@pytest.mark.asyncio
async def test_hard_scan_skips_same_day_positions(tmp_path, monkeypatch):
    """测试 hard_scan 自动跳过当日建仓的标的，不触发止损平仓。"""
    brain = _MockBrain(tmp_path)
    today = date.today().isoformat()

    # 今日买入且价格跌破止损线
    brain.store.open_position(
        position_id="POS-HARD-T0",
        symbol="600383",
        name="金地集团",
        entry_price=2.50,
        stop_loss=2.40,
        qty=1000,
        entry_date=today,
        persona_id="short_term_hot_rotation_v1",
    )

    monkeypatch.setattr(
        "src.agents.reviewer.hard_scan.get_central_brain",
        lambda: brain,
    )
    monkeypatch.setattr(
        "src.agents.reviewer.hard_scan._price_for",
        lambda symbol, pos, intraday, cache: (2.30, False),  # 2.30 < 2.40 止损价
    )
    monkeypatch.setattr(
        "src.agents.reviewer.intraday_loop.is_trading_hours",
        lambda: True,
    )

    exits = await run_hard_scan(persona_id="short_term_hot_rotation_v1", force=False)
    # 当日持仓因 T+1 锁定，exits 中不应包含该清仓动作
    assert len(exits) == 0

    open_pos = brain.store.list_open_positions(persona_id="short_term_hot_rotation_v1")
    assert len(open_pos) == 1
    assert open_pos[0]["symbol"] == "600383"


def test_position_reviewer_rule_override_t1_protection():
    """测试 position_reviewer 当日建仓标的强制将 EXIT/REDUCE 动作降为 HOLD。"""
    agent = PositionReviewAgent(persona_id="short_term_hot_rotation_v1")
    today = date.today().isoformat()

    pos = {
        "symbol": "600383",
        "entry_price": 2.50,
        "stop_loss": 2.40,
        "entry_date": today,
    }
    # LLM 或规则给出 EXIT
    initial_result = {"action": "EXIT", "reason": "破位"}
    overridden = agent._apply_rule_override(
        result=initial_result,
        position=pos,
        current_price=2.30,
        pnl_pct=-8.0,
    )

    assert overridden["action"] == "HOLD"
    assert "T+1" in overridden["reason"]


def test_intraday_client_quote_fallback(monkeypatch):
    """测试当分钟K线拉取失败时，通过新浪秒级实时行情兜底合成快照。"""
    client = IntradayClient(allow_mock_fallback=False)

    # 模拟分钟K线抛出异常 (如网络断连)
    monkeypatch.setattr(
        client,
        "fetch_minute_bars",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Connection aborted")),
    )

    # 模拟实时行情返回有效报价
    fake_quote = {
        "name": "金地集团",
        "open": 2.47,
        "prev_close": 2.55,
        "current_price": 2.74,
        "high": 2.81,
        "low": 2.30,
        "volume": 4918583.3,
        "turnover": 1258907881.0,
        "change_pct": 7.45,
    }
    monkeypatch.setattr(client, "_fetch_quote_sina", lambda symbol: fake_quote)

    snap = client.fetch_intraday_snapshot("600383")
    assert snap.is_mock is False
    assert snap.current_price == 2.74
    assert snap.prev_close == 2.55
    assert snap.high == 2.81
    assert snap.low == 2.30
    assert len(snap.bars) == 1
    assert snap.bars[0].close == 2.74
    assert client._last_source == "sina_quote"

