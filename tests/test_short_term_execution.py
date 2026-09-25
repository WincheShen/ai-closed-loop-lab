"""短线高弹性动态自选股 + 尾盘狙击 (14:35) + 早盘脉冲 (09:35) 单元测试。"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from src.agents.explorer.short_term_watchlist import (
    MAX_WATCH_DAYS,
    TARGET_POOL_MAX,
    ShortTermWatchlistManager,
)
from src.agents.reviewer.tail_hunt import (
    check_cold_period,
    execute_force_close_ultra_short,
    execute_morning_pulse,
    execute_tail_hunt,
)
from src.central_brain.metadata_store import MemoryStore
from src.stock_analyzer.data_source.intraday_client import IntradaySnapshot, MinuteBar


class _DummyBus:
    def emit_order_fill(self, *args, **kwargs):
        pass


class _MockBrain:
    def __init__(self, tmp_path):
        self.store = MemoryStore(str(tmp_path / "test_st.db"))
        self.bus = _DummyBus()


def _make_snapshot(price: float, is_mock: bool = False, turnover: float = 4e8, change_pct: float = 2.0) -> IntradaySnapshot:
    bars = [
        MinuteBar(
            timestamp=datetime(2026, 9, 25, 14, 0),
            open=price * 0.98, high=price * 1.02, low=price * 0.97,
            close=price, volume=10000, turnover=turnover,
        ),
        MinuteBar(
            timestamp=datetime(2026, 9, 25, 14, 30),
            open=price * 0.99, high=price * 1.02, low=price * 0.98,
            close=price, volume=10000, turnover=turnover,
        ),
    ]
    return IntradaySnapshot(
        symbol="300209", name="行云科技", current_price=price,
        prev_close=price * 0.98, change_pct=change_pct,
        high=price * 1.02, low=price * 0.97, open=price * 0.98,
        volume=20000, turnover=turnover, bars=bars, is_mock=is_mock,
    )


# ── ShortTermWatchlistManager 单元测试 ────────────────────────

def test_watchlist_add_and_filter(tmp_path):
    brain = _MockBrain(tmp_path)
    mgr = ShortTermWatchlistManager(brain)

    # 1. 允许的高弹性板块 (创业板 300)
    assert mgr.add_candidate("300209", "行云科技", price=25.0) is True
    # 重复添加应被拒绝
    assert mgr.add_candidate("300209", "行云科技", price=25.0) is False

    # 2. 禁止的板块 (沪市主板 600)
    assert mgr.add_candidate("600519", "贵州茅台", price=1300.0) is False

    # 3. 过滤 ST 股票
    assert mgr.add_candidate("300001", "*ST特尔", price=5.0) is False

    pool = mgr.get_active_pool()
    assert len(pool) == 1
    assert pool[0]["symbol"] == "300209"
    assert pool[0]["strategy_id"] == "ultra_short_momentum"


def test_watchlist_rebound_watch_and_capacity(tmp_path):
    brain = _MockBrain(tmp_path)
    mgr = ShortTermWatchlistManager(brain)

    # 复刻行云科技式回流
    assert mgr.add_rebound_watch("300209", "行云科技", last_price=24.5, reason="平仓回流跟踪") is True
    pool = mgr.get_active_pool()
    assert len(pool) == 1
    assert pool[0]["source"] == "rebound_watch"

    # 容量上限保护
    for i in range(TARGET_POOL_MAX + 5):
        mgr.add_candidate(f"300{i:03d}", f"标的{i}", price=10.0)

    symbols = mgr.get_active_symbols()
    assert len(symbols) <= TARGET_POOL_MAX


def test_watchlist_audit_and_clean(tmp_path):
    brain = _MockBrain(tmp_path)
    mgr = ShortTermWatchlistManager(brain)

    mgr.add_candidate("300001", "超期标的", price=10.0)
    mgr.add_candidate("300002", "破位标的", price=20.0)
    mgr.add_candidate("300003", "缩量标的", price=15.0)
    mgr.add_candidate("300004", "弹性枯竭标的", price=18.0)
    mgr.add_candidate("300005", "健康标的", price=25.0)

    # 手动修改 300001 的 days_watched 超过 MAX_WATCH_DAYS
    conn = brain.store._conn()
    conn.execute("UPDATE watchlist SET days_watched = ? WHERE symbol = '300001'", (MAX_WATCH_DAYS + 1,))
    conn.commit()

    quotes = {
        "300002": {"price": 18.5, "turnover": 4e8, "amplitude": 0.08, "change_pct": -7.5},  # 单日跌超 -6%
        "300003": {"price": 15.0, "turnover": 0.8e8, "amplitude": 0.06, "change_pct": 1.0},  # 成交额 < 1.5亿
        "300004": {"price": 18.0, "turnover": 3e8, "amplitude": 0.02, "change_pct": 0.5},    # 振幅 < 3%
        "300005": {"price": 25.5, "turnover": 5e8, "amplitude": 0.07, "change_pct": 2.5},    # 健康
    }

    res = mgr.audit_and_clean(quotes)
    assert res["checked"] == 5
    assert res["removed"] == 4  # 4 个应该被剔除
    assert res["remaining"] == 1

    remaining_symbols = mgr.get_active_symbols()
    assert remaining_symbols == ["300005"]


# ── 盘后维护闭环（清洗 + 补票）─────────────────────────────────

class _FakeQuote:
    def __init__(self, symbol, name, price, change_pct, turnover, turnover_rate, industry="电子"):
        self.symbol = symbol
        self.name = name
        self.price = price
        self.change_pct = change_pct
        self.turnover = turnover
        self.turnover_rate = turnover_rate
        self.industry = industry


class _FakeSnapshot:
    def __init__(self, stocks, is_mock=False):
        self.stocks = stocks
        self.is_mock = is_mock


class _FakeKlineClient:
    def fetch_kline(self, symbol, days=3):
        raise RuntimeError("test: no kline")


def test_run_daily_maintenance_feeds_pool(tmp_path):
    brain = _MockBrain(tmp_path)
    mgr = ShortTermWatchlistManager(brain)

    snapshot = _FakeSnapshot([
        _FakeQuote("300209", "行云科技", 25.0, 4.0, 5e8, 12.0),      # 合规入池
        _FakeQuote("688981", "中芯国际", 90.0, 3.0, 8e8, 9.0),       # 合规入池
        _FakeQuote("600519", "贵州茅台", 1300.0, 2.0, 9e8, 15.0),    # 板块前缀拒绝
        _FakeQuote("300999", "*ST测试", 5.0, 3.0, 5e8, 12.0),        # ST 拒绝
        _FakeQuote("301888", "缩量标的", 20.0, 2.0, 1e8, 12.0),      # 成交额 < 3 亿拒绝
        _FakeQuote("300777", "低换手", 30.0, 2.0, 5e8, 3.0),         # 换手率 < 8% 拒绝
        _FakeQuote("300555", "过热标的", 40.0, 19.5, 5e8, 25.0),     # 涨幅 > 11% 拒绝（不追涨停）
        _FakeQuote("300666", "崩坏标的", 10.0, -8.0, 5e8, 15.0),     # 跌幅 < -4% 拒绝（不接飞刀）
        _FakeQuote("301111", "新浪降级高成交", 15.0, 3.0, 6e8, 0.0), # 换手率缺失但成交 >= 5亿 → 入池
        _FakeQuote("302222", "新浪降级低成交", 15.0, 3.0, 4e8, 0.0), # 换手率缺失且成交 < 5亿 → 拒绝
    ])

    res = mgr.run_daily_maintenance(snapshot=snapshot, client=_FakeKlineClient())

    assert res["data_ok"] is True
    assert res["added"] == 3
    assert res["pool_size"] == 3
    assert set(mgr.get_active_symbols()) == {"300209", "688981", "301111"}


def test_run_daily_maintenance_cleans_with_real_quotes(tmp_path):
    brain = _MockBrain(tmp_path)
    mgr = ShortTermWatchlistManager(brain)
    mgr.add_candidate("300209", "行云科技", price=25.0)
    mgr.add_candidate("300684", "中石科技", price=50.0)

    snapshot = _FakeSnapshot([
        _FakeQuote("300209", "行云科技", 23.0, -7.5, 5e8, 12.0),   # 破位暴跌 → 踢出
        _FakeQuote("300684", "中石科技", 51.0, 2.0, 5e8, 10.0),     # 健康 → 保留并回写检查价
    ])

    res = mgr.run_daily_maintenance(snapshot=snapshot, client=_FakeKlineClient())

    assert res["data_ok"] is True
    assert res["audited"]["removed"] == 1
    assert mgr.get_active_symbols() == ["300684"]
    # 健康标的被回写最新检查价
    item = mgr.get_active_pool()[0]
    assert item["last_price"] == 51.0


def test_run_daily_maintenance_mock_snapshot_blocks_feed(tmp_path):
    brain = _MockBrain(tmp_path)
    mgr = ShortTermWatchlistManager(brain)

    snapshot = _FakeSnapshot(
        [_FakeQuote("300209", "行云科技", 25.0, 4.0, 5e8, 12.0)],
        is_mock=True,
    )
    res = mgr.run_daily_maintenance(snapshot=snapshot, client=_FakeKlineClient())

    assert res["data_ok"] is False
    assert res["added"] == 0
    assert res["pool_size"] == 0


# ── 冷静期与大盘天气前置闸门 ─────────────────────────────────

def test_check_cold_period_post_loss(tmp_path):
    brain = _MockBrain(tmp_path)

    # 无平仓历史：不触发冷静期
    is_cold, reason = check_cold_period(brain, "ultra_short_momentum_v1")
    assert is_cold is False

    # 昨天平仓且大亏 -4.5%：触发冷静期
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    brain.store.open_position(
        position_id="POS-TEST-1",
        symbol="300209",
        entry_price=10.0,
        qty=1000,
        entry_date=yesterday,
        persona_id="ultra_short_momentum_v1",
    )
    brain.store.close_position("POS-TEST-1", close_price=9.55, realized_pnl=-450.0)

    is_cold, reason = check_cold_period(brain, "ultra_short_momentum_v1")
    assert is_cold is True
    assert "触发止损" in reason or "冷静期" in reason


def test_check_cold_period_bad_weather(tmp_path):
    brain = _MockBrain(tmp_path)
    conn = brain.store._conn()

    # 市场上涨比例 20% (< 30%)
    today = date.today().isoformat()
    conn.execute(
        """INSERT INTO market_regime_snapshots
        (snapshot_id, trade_date, regime, recommended_posture, max_total_position_pct, evidence_json, created_at)
        VALUES ('SNAP-1', ?, 'bear', 'defend', 0.15, ?, ?)""",
        (today, json.dumps({"up_ratio": 0.20}), datetime.now().isoformat()),
    )
    conn.commit()

    is_cold, reason = check_cold_period(brain, "ultra_short_momentum_v1")
    assert is_cold is True
    assert "低于 30%" in reason or "恶劣天气" in reason


# ── 14:35 尾盘狙击 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_tail_hunt_cold_skip(tmp_path):
    brain = _MockBrain(tmp_path)
    # 注入恐慌大盘天气
    conn = brain.store._conn()
    conn.execute(
        """INSERT INTO market_regime_snapshots
        (snapshot_id, trade_date, regime, recommended_posture, max_total_position_pct, evidence_json, created_at)
        VALUES ('SNAP-PANIC', ?, 'panic', 'exit', 0.0, '{}', ?)""",
        (date.today().isoformat(), datetime.now().isoformat()),
    )
    conn.commit()

    res = await execute_tail_hunt(brain, "ultra_short_momentum_v1")
    assert not res["executed"]
    assert res["action"] == "SKIP"


@pytest.mark.asyncio
async def test_execute_tail_hunt_success(tmp_path, monkeypatch):
    brain = _MockBrain(tmp_path)
    wl_mgr = ShortTermWatchlistManager(brain)
    wl_mgr.add_candidate("300209", "行云科技", price=25.0)

    # 模拟正常大盘天气 (up_ratio=0.55)
    conn = brain.store._conn()
    conn.execute(
        """INSERT INTO market_regime_snapshots
        (snapshot_id, trade_date, regime, recommended_posture, max_total_position_pct, evidence_json, created_at)
        VALUES ('SNAP-OK', ?, 'rebound', 'selective_attack', 0.60, ?, ?)""",
        (date.today().isoformat(), json.dumps({"up_ratio": 0.55}), datetime.now().isoformat()),
    )
    conn.commit()

    # 模拟盘中行情抓取
    monkeypatch.setattr(
        "src.agents.reviewer.tail_hunt.IntradayClient.fetch_intraday_snapshot",
        lambda self, symbol, **kwargs: _make_snapshot(26.0, is_mock=False, turnover=5e8, change_pct=4.0),
    )

    res = await execute_tail_hunt(brain, "ultra_short_momentum_v1")
    assert res["executed"] is True
    assert res["symbol"] == "300209"
    assert res["quantity"] > 0

    # 检查持仓是否创建
    open_positions = brain.store.list_open_positions(persona_id="ultra_short_momentum_v1")
    assert len(open_positions) == 1
    assert open_positions[0]["symbol"] == "300209"
    assert open_positions[0]["strategy_id"] == "ultra_short_momentum"


# ── 09:35 早盘脉冲 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_morning_pulse_take_profit(tmp_path, monkeypatch):
    brain = _MockBrain(tmp_path)
    # 先建立一个底仓
    brain.store.open_position(
        position_id="POS-PULSE-1",
        symbol="300209",
        name="行云科技",
        entry_price=20.0,
        qty=1000,
        entry_date=date.today().isoformat(),
        persona_id="ultra_short_momentum_v1",
        target_price=21.6,
        stop_loss=19.5,
    )

    # 模拟早盘高开冲高 +6% (21.2)
    monkeypatch.setattr(
        "src.agents.reviewer.tail_hunt.IntradayClient.fetch_intraday_snapshot",
        lambda self, symbol, **kwargs: _make_snapshot(21.2, is_mock=False, turnover=3e8, change_pct=6.0),
    )

    res = await execute_morning_pulse(brain, "ultra_short_momentum_v1")
    assert res["checked"] == 1
    assert len(res["actions"]) == 1
    assert res["actions"][0]["action"] == "EXIT"

    # 检查持仓是否平仓
    open_pos = brain.store.list_open_positions(persona_id="ultra_short_momentum_v1")
    assert len(open_pos) == 0

    # 检查行云科技是否回流到了自选股跟踪池
    wl_mgr = ShortTermWatchlistManager(brain)
    pool = wl_mgr.get_active_pool()
    assert any(item["symbol"] == "300209" and item["source"] == "rebound_watch" for item in pool)


# ── 14:30 次日强制清仓 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_force_close_ultra_short(tmp_path, monkeypatch):
    brain = _MockBrain(tmp_path)
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    brain.store.open_position(
        position_id="POS-YESTERDAY-1",
        symbol="300684",
        name="中石科技",
        entry_price=15.0,
        qty=1000,
        entry_date=yesterday,
        persona_id="ultra_short_momentum_v1",
    )

    monkeypatch.setattr(
        "src.agents.reviewer.tail_hunt.IntradayClient.fetch_intraday_snapshot",
        lambda self, symbol, **kwargs: _make_snapshot(15.2, is_mock=False),
    )

    res = await execute_force_close_ultra_short(brain, "ultra_short_momentum_v1")
    assert res["closed"] == 1

    open_pos = brain.store.list_open_positions(persona_id="ultra_short_momentum_v1")
    assert len(open_pos) == 0
