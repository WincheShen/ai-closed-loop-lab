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


@pytest.fixture(autouse=True)
def _legacy_tail_hunt_wheel_mode(monkeypatch):
    """旧路径尾盘狙击单测：转盘绑定设为 off，使 execute_tail_hunt 验证旧代码内部逻辑。"""
    from types import SimpleNamespace
    import src.wheel.config as wc
    try:
        real_cfg = wc.get_config()
        monkeypatch.setattr(
            wc, "get_config",
            lambda *a, **k: SimpleNamespace(
                binding_for=lambda pid, et: None if et == "clock.1435" else real_cfg.binding_for(pid, et),
                personas=real_cfg.personas, wheels=real_cfg.wheels,
            ),
        )
    except Exception:
        pass


class _MockBrain:
    def __init__(self, tmp_path):
        self.store = MemoryStore(str(tmp_path / "test_st.db"))
        self.bus = _DummyBus()

    def log_agent_event(self, **kwargs):
        pass


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


def test_check_cold_period_friday_to_monday(tmp_path, monkeypatch):
    """验证周五大亏平仓，跨周末至周一依然严格保持 1 个交易日冷静期。"""
    brain = _MockBrain(tmp_path)
    monday = date(2026, 9, 21)
    friday = date(2026, 9, 18)

    class MockDate(date):
        @classmethod
        def today(cls):
            return monday

    monkeypatch.setattr("src.agents.reviewer.tail_hunt.date", MockDate)

    brain.store.open_position(
        position_id="POS-FRI-1",
        symbol="300209",
        entry_price=10.0,
        qty=1000,
        entry_date=friday.isoformat(),
        persona_id="ultra_short_momentum_v1",
    )
    brain.store.close_position("POS-FRI-1", close_price=9.50, realized_pnl=-500.0)
    brain.store._conn().execute(
        "UPDATE positions SET closed_at=? WHERE position_id='POS-FRI-1'",
        (friday.isoformat() + "T14:35:00",),
    )

    is_cold, reason = check_cold_period(brain, "ultra_short_momentum_v1")
    assert is_cold is True
    assert "冷静期" in reason


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

    # 模拟盘中行情抓取（同时隔离真实日线网络请求）
    monkeypatch.setattr(
        "src.agents.reviewer.tail_hunt.IntradayClient.fetch_intraday_snapshot",
        lambda self, symbol, **kwargs: _make_snapshot(26.0, is_mock=False, turnover=5e8, change_pct=4.0),
    )
    monkeypatch.setattr(
        "src.agents.reviewer.tail_hunt.AkshareClient.fetch_kline",
        lambda self, symbol, days=25: [],
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
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    brain.store.open_position(
        position_id="POS-PULSE-1",
        symbol="300209",
        name="行云科技",
        entry_price=20.0,
        qty=1000,
        entry_date=yesterday,
        persona_id="ultra_short_momentum_v1",
        target_price=21.6,
        stop_loss=19.5,
    )

    # 模拟早盘高开冲高 +6% (21.2)
    monkeypatch.setattr(
        "src.stock_analyzer.data_source.intraday_client.IntradayClient.fetch_intraday_snapshot",
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


# ── 2026-10-19 002463 沪电股份假红盘/无动能回归测试 ─────────────

def test_watchlist_audit_micro_drop_and_momentum_decay(tmp_path):
    """回归：微跌 -0.66% 不得被当成 -66% 误踢；20日涨幅转负应被踢出。"""
    brain = _MockBrain(tmp_path)
    mgr = ShortTermWatchlistManager(brain)

    mgr.add_candidate("688041", "海光信息", price=192.54)
    mgr.add_candidate("002463", "沪电股份", price=113.38)

    quotes = {
        "688041": {
            "price": 192.54,
            "turnover": 39.5e8,
            "amplitude": 0.055,
            "change_pct": -0.66,  # 微跌 -0.66%，不应触发 -6% 破位踢出
            "ret5": 0.08,
            "ret20": 0.15,
        },
        "002463": {
            "price": 113.38,
            "turnover": 52.9e8,
            "amplitude": 0.068,
            "change_pct": -2.41,
            "ret5": 0.023,
            "ret20": -0.1255,     # 20日跌幅 -12.55%，中期动量丧失应踢出
        },
    }

    res = mgr.audit_and_clean(quotes)
    assert res["checked"] == 2
    assert res["removed"] == 1
    assert mgr.get_active_symbols() == ["688041"]


@pytest.mark.asyncio
async def test_execute_tail_hunt_rejects_weak_momentum_002463(tmp_path, monkeypatch):
    """回归 2026-10-19 002463 沪电股份：
    库内昨收 113.38、20日涨幅 -12.55%、14:35 现价 112.95（真实跌 -0.38%），
    即便日内低开 112.20 拉回至振幅高位区 (close_pos=0.85)，也必须因绿盘+多日动能不足被拒绝。
    """
    from src.stock_analyzer.data_source.akshare_client import KlineBar

    brain = _MockBrain(tmp_path)
    wl_mgr = ShortTermWatchlistManager(brain)
    wl_mgr.add_candidate("002463", "沪电股份", price=113.38)

    conn = brain.store._conn()
    conn.execute(
        """INSERT INTO market_regime_snapshots
        (snapshot_id, trade_date, regime, recommended_posture, max_total_position_pct, evidence_json, created_at)
        VALUES ('SNAP-1019', ?, 'neutral', 'selective_attack', 0.40, ?, ?)""",
        (date.today().isoformat(), json.dumps({"up_ratio": 0.438}), datetime.now().isoformat()),
    )
    conn.commit()

    today_dt = datetime(2026, 10, 19, 14, 35)
    # 模拟盘中快照（即使 prev_close 被旧逻辑误传为开盘价 112.20）
    snap_002463 = IntradaySnapshot(
        symbol="002463",
        name="沪电股份",
        current_price=112.95,
        prev_close=112.20,
        change_pct=0.67,
        high=114.20,
        low=106.06,
        open=112.20,
        volume=419729,
        turnover=46.4e8,
        bars=[
            MinuteBar(
                timestamp=datetime(2026, 10, 19, 10, 0),
                open=112.20, high=113.32, low=106.06, close=108.47,
                volume=200000, turnover=26.6e8,
            ),
            MinuteBar(
                timestamp=today_dt,
                open=113.96, high=114.20, low=112.95, close=112.95,
                volume=219729, turnover=19.8e8,
            ),
        ],
        is_mock=False,
    )

    # 构造行情库中的 22 根历史日线：20日前收盘 126.0，5日前收盘 110.0，昨日(10-16)收盘 113.38
    hist_klines = []
    base_d = date(2026, 10, 19)
    for i in range(22, 0, -1):
        d = base_d - timedelta(days=i)
        c = 126.0 if i >= 20 else (110.0 if i == 5 else (113.38 if i == 1 else 115.0))
        hist_klines.append(
            KlineBar(
                date=d, open=c, high=c * 1.02, low=c * 0.98, close=c,
                volume=500000, turnover=50e8,
            )
        )

    monkeypatch.setattr(
        "src.agents.reviewer.tail_hunt.IntradayClient.fetch_intraday_snapshot",
        lambda self, symbol, **kwargs: snap_002463,
    )
    monkeypatch.setattr(
        "src.agents.reviewer.tail_hunt.AkshareClient.fetch_kline",
        lambda self, symbol, days=25: hist_klines,
    )

    res = await execute_tail_hunt(brain, "ultra_short_momentum_v1")
    assert res["executed"] is False
    assert res["action"] == "SKIP"
    assert len(brain.store.list_open_positions(persona_id="ultra_short_momentum_v1")) == 0


def test_check_cold_period_ignores_small_pct_loss_over_1000_yuan(tmp_path):
    """复刻 2026-10-20 002463 场景：亏损 -1.97% (> -2.5%) 但金额 -1115 元，不应触发止损冷静期。"""
    brain = _MockBrain(tmp_path)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    brain.store.open_position(
        position_id="POS-002463",
        symbol="002463",
        entry_price=112.95,
        qty=500,
        entry_date=yesterday,
        persona_id="ultra_short_momentum_v1",
    )
    brain.store.close_position("POS-002463", close_price=110.72, realized_pnl=-1115.0)

    is_cold, _ = check_cold_period(brain, "ultra_short_momentum_v1")
    assert is_cold is False


def test_risk_decision_syncs_signal_status_and_blocks_pending(tmp_path):
    """回归：RiskGovernor 拒绝或降仓时同步更新 trade_signals，防止被拒条件单穿透到 _check_pending_signals。"""
    brain = _MockBrain(tmp_path)
    now_iso = datetime.now().isoformat()

    brain.store.save_trade_signal("sess-1", {
        "signal_id": "SIG-REJECT-1",
        "symbol": "600887",
        "action": "buy",
        "entry_price": 27.0,
        "target_price": 41.6,
        "stop_loss": 22.2,
        "position_pct": 0.12,
        "strategy": "价值投资",
        "rationale": "test",
        "timestamp": now_iso,
        "entry_condition": "pullback",
        "current_price": 27.76,
        "persona_id": "duan_yongping_v1",
    })
    brain.store.save_trade_signal("sess-1", {
        "signal_id": "SIG-REDUCE-1",
        "symbol": "301591",
        "action": "buy",
        "entry_price": 44.5,
        "target_price": 50.0,
        "stop_loss": 40.94,
        "position_pct": 0.08,
        "strategy": "主力吸筹",
        "rationale": "test",
        "timestamp": now_iso,
        "entry_condition": "pullback",
        "current_price": 45.68,
        "persona_id": "short_term_hot_rotation_v1",
    })

    brain.store.save_risk_decision("sess-1", {
        "signal_id": "SIG-REJECT-1",
        "symbol": "600887",
        "decision": "reject",
        "original_position_pct": 0.12,
        "approved_position_pct": 0.0,
        "reason": "总仓位已达 40% 上限",
        "risk_flags": ["total_position_exceeded"],
        "persona_id": "duan_yongping_v1",
    })
    brain.store.save_risk_decision("sess-1", {
        "signal_id": "SIG-REDUCE-1",
        "symbol": "301591",
        "decision": "reduce",
        "original_position_pct": 0.08,
        "approved_position_pct": 0.04,
        "reason": "仓位减半",
        "risk_flags": ["weak_regime_haircut"],
        "persona_id": "short_term_hot_rotation_v1",
    })

    pending = brain.store.list_pending_signals()
    pending_ids = [s["signal_id"] for s in pending]
    assert "SIG-REJECT-1" not in pending_ids
    assert "SIG-REDUCE-1" in pending_ids
    reduced_sig = next(s for s in pending if s["signal_id"] == "SIG-REDUCE-1")
    assert reduced_sig["position_pct"] == pytest.approx(0.04)
    assert reduced_sig["approved_position_pct"] == pytest.approx(0.04)
    assert reduced_sig["risk_decision"] == "reduce"


def test_fundamental_client_enriches_pe_pb_fcf_and_mcap(tmp_path, monkeypatch):
    """验证 FundamentalClient 能从财务指标推算并补齐 PE(TTM)、PB、FCF 收益率与市值。"""
    import pandas as pd
    import src.stock_analyzer.data_source.fundamental_client as fc_mod
    from src.stock_analyzer.data_source.akshare_client import StockQuote

    monkeypatch.setattr(fc_mod, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(fc_mod, "_CACHE_DB", tmp_path / "fundamentals.db")

    df = pd.DataFrame([
        {
            "日期": date(2025, 6, 30),
            "摊薄每股收益(元)": 37.40,
            "每股净资产_调整前(元)": 198.33,
            "每股经营性现金流(元)": 10.44,
            "加权净资产收益率(%)": 17.89,
            "资产负债率(%)": 14.75,
            "总资产(元)": 2.92e11,
        },
        {
            "日期": date(2025, 12, 31),
            "摊薄每股收益(元)": 68.12,
            "每股净资产_调整前(元)": 202.80,
            "每股经营性现金流(元)": 49.13,
            "加权净资产收益率(%)": 32.53,
            "资产负债率(%)": 16.42,
            "总资产(元)": 3.04e11,
        },
        {
            "日期": date(2026, 6, 30),
            "摊薄每股收益(元)": 36.82,
            "每股净资产_调整前(元)": 209.66,
            "每股经营性现金流(元)": 56.55,
            "加权净资产收益率(%)": 16.75,
            "资产负债率(%)": 15.19,
            "总资产(元)": 3.09e11,
        },
    ])

    client = fc_mod.FundamentalClient()
    eps_ttm = client._compute_ttm_per_share(df, ("摊薄每股收益(元)",))
    ocf_ttm = client._compute_ttm_per_share(df, ("每股经营性现金流(元)",))
    # TTM = 2025年报 + 2026中报 - 2025中报
    assert eps_ttm == pytest.approx(68.12 + 36.82 - 37.40, rel=1e-3)
    assert ocf_ttm == pytest.approx(49.13 + 56.55 - 10.44, rel=1e-3)

    monkeypatch.setattr(
        fc_mod.FundamentalClient,
        "_fetch_batch",
        lambda self, symbols: {
            "600519": fc_mod.FundamentalMetrics(
                symbol="600519",
                roe=32.53,
                debt_to_equity=0.1642,
                dividend_yield=30.0,
                report_date="2025-12-31",
                eps_ttm=eps_ttm,
                bps=209.66,
                ocf_per_share=ocf_ttm,
                total_shares=1.25e9,
            )
        },
    )

    q = StockQuote(
        symbol="600519", name="贵州茅台", price=1500.0, change_pct=0.5,
        volume=10000, turnover=1.5e9,
    )
    client.enrich_quotes([q])
    assert q.roe == pytest.approx(32.53)
    assert q.pe_ttm == pytest.approx(1500.0 / eps_ttm, abs=0.05)
    assert q.pb == pytest.approx(1500.0 / 209.66, abs=0.05)
    assert q.fcf_yield == pytest.approx(ocf_ttm / 1500.0 * 100, abs=0.05)
    assert q.dividend_yield == pytest.approx(2.0, abs=0.05)
    assert q.market_cap_yi == pytest.approx(18750.0, abs=1.0)


