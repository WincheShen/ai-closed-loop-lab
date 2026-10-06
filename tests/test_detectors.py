"""检测器 fixture：命中 / 不命中。"""
from __future__ import annotations

from datetime import date, timedelta

from src.stock_analyzer.data_source.akshare_client import KlineBar
from src.stock_analyzer.detectors.bottom_reversal import detect as detect_bottom
from src.stock_analyzer.detectors.hot_sector_pullback import detect as detect_pullback
from src.stock_analyzer.detectors.types import BarContext
from src.stock_analyzer.detectors.ultra_short_momentum import detect as detect_momentum
from src.stock_analyzer.detectors.value_investing import detect as detect_value
from src.strategy_lab.engine import run_backtest


def _bars(closes: list[float], start: date = date(2024, 1, 2)) -> list[KlineBar]:
    out = []
    d = start
    for i, c in enumerate(closes):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        prev = closes[i - 1] if i else c
        high = max(c, prev) * 1.01
        low = min(c, prev) * 0.99
        out.append(
            KlineBar(
                date=d, open=prev, high=high, low=low, close=c,
                volume=10000 + i * 100, turnover=1e8 + i,
                change_pct=(c / prev - 1) * 100 if prev else 0,
            )
        )
        d += timedelta(days=1)
    return out


def test_pullback_hit_and_miss() -> None:
    # 先抬高再回撤约 8%
    closes = [10 + i * 0.2 for i in range(16)] + [12.2, 12.0, 11.8, 11.6]
    bars = _bars(closes)
    ctx = BarContext("000001", bars, sector="半导体", hot_sectors=("半导体",))
    hit = detect_pullback(ctx)
    assert hit is not None
    assert hit.strategy_id == "hot_sector_pullback"
    miss = detect_pullback(BarContext("000001", bars, sector="白酒", hot_sectors=("半导体",)))
    assert miss is None


def test_bottom_reversal_requires_cross() -> None:
    # 连跌后一根放量阳线但均线未金叉 → 不命中
    closes = [20 - i * 0.4 for i in range(10)] + [16.2]
    bars = _bars(closes)
    # 放大最后一根量
    last = bars[-1]
    bars[-1] = KlineBar(
        date=last.date, open=15.5, high=16.4, low=15.4, close=16.2,
        volume=1e6, turnover=2e8,
    )
    assert detect_bottom(BarContext("000002", bars)) is None


def test_value_skips_new_high() -> None:
    closes = [10 + i * 0.3 for i in range(20)]
    bars = _bars(closes)
    assert detect_value(BarContext("600519", bars, pe_ttm=18)) is None


def test_backtest_deterministic() -> None:
    closes = [10 + i * 0.2 for i in range(16)] + [12.2, 12.0, 11.8, 11.6] + [11.5] * 15
    bars = _bars(closes)
    r1 = run_backtest(
        {"000001": bars},
        "hot_sector_pullback",
        sector_of={"000001": "半导体"},
        hot_by_date={str(b.date): ("半导体",) for b in bars},
    )
    r2 = run_backtest(
        {"000001": bars},
        "hot_sector_pullback",
        sector_of={"000001": "半导体"},
        hot_by_date={str(b.date): ("半导体",) for b in bars},
    )
    assert r1.n == r2.n
    assert [f.pnl_pct for f in r1.fills] == [f.pnl_pct for f in r2.fills]


def test_synthetic_backtest_reaches_sample_and_is_deterministic() -> None:
    from src.strategy_lab.gates import evaluate_gates
    from src.strategy_lab.synthetic import universe_for

    series = universe_for("hot_sector_pullback", n_symbols=20)
    hot = {str(b.date): ("半导体",) for bars in series.values() for b in bars}
    sector = {s: "半导体" for s in series}
    r1 = run_backtest(series, "hot_sector_pullback", sector_of=sector, hot_by_date=hot)
    r2 = run_backtest(series, "hot_sector_pullback", sector_of=sector, hot_by_date=hot)
    assert r1.n == r2.n
    assert r1.n >= 200
    assert [f.pnl_pct for f in r1.fills] == [f.pnl_pct for f in r2.fills]
    # 合成样本只证明引擎能跑到 G-bt-2，不算研究过关
    assert any(g.name == "G-bt-2" and g.ok for g in evaluate_gates(r1))


# ---------------------------------------------------------------------------
# ultra_short_momentum
# ---------------------------------------------------------------------------

def _spec_bars(
    specs: list[tuple[float, float, float, float, float]],
    start: date = date(2024, 1, 2),
) -> list[KlineBar]:
    """specs: (open, high, low, close, turnover)。"""
    out: list[KlineBar] = []
    d = start
    for o, h, lo, c, to in specs:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(
            KlineBar(date=d, open=o, high=h, low=lo, close=c, volume=3e4, turnover=to)
        )
        d += timedelta(days=1)
    return out


def _continuation_specs() -> list[tuple[float, float, float, float, float]]:
    """18 日慢涨 + 3 日放量急涨收高位 → 触发 momentum_continuation。"""
    specs = []
    for i in range(18):
        c = 20 * (1 + 0.008 * i)
        o = c * 0.997
        specs.append((o, c * 1.01, o * 0.99, c, 4e8))
    base = specs[-1][3]
    for step in range(3):
        o = base * (1 + 0.035 * step)
        c = base * (1 + 0.035 * (step + 1))
        specs.append((o, c * 1.03, o * 0.965, c, 6e8))
    return specs


def _pullback_specs() -> list[tuple[float, float, float, float, float]]:
    """17 日强势上涨 + 4 日宽幅回踩且末日收阳 → 触发 strong_pullback。"""
    specs = []
    for i in range(17):
        c = 20 * (1.02 ** i)
        o = c * 0.997
        specs.append((o, c * 1.01, o * 0.99, c, 4e8))
    peak = specs[-1][3]
    prev = peak
    for k in range(1, 5):
        c = peak * (1 - 0.022 * k)
        o = c * 0.995  # 收阳企稳
        specs.append((o, prev * 1.008, c * 0.94, c, 4e8))
        prev = c
    return specs


def test_ultra_short_continuation_hit() -> None:
    bars = _spec_bars(_continuation_specs())
    hit = detect_momentum(BarContext("300418", bars))
    assert hit is not None
    assert hit.strategy_id == "ultra_short_momentum"
    assert hit.evidence["setup"] == "momentum_continuation"
    assert hit.entry_condition == "immediate"
    assert hit.position_pct_cap == 0.12
    assert hit.stop_loss < hit.entry_price
    assert hit.entry_price * 0.96 <= hit.stop_loss  # 止损不超 3.5%
    assert hit.target_price == round(hit.entry_price * 1.08, 2)


def test_ultra_short_pullback_hit() -> None:
    bars = _spec_bars(_pullback_specs())
    hit = detect_momentum(BarContext("300684", bars))
    assert hit is not None
    assert hit.evidence["setup"] == "strong_pullback"
    assert hit.position_pct_cap == 0.08
    assert hit.stop_loss < hit.entry_price


def test_ultra_short_board_and_st_filters() -> None:
    bars = _spec_bars(_continuation_specs())
    # 沪市主板不在高弹性池
    assert detect_momentum(BarContext("600519", bars)) is None
    # 基金代码段不在池内
    assert detect_momentum(BarContext("159915", bars)) is None
    # ST / 退市股剔除
    assert detect_momentum(BarContext("300418", bars, name="*ST测试")) is None
    # 创业板/科创板/北交所/深市主板允许
    for sym in ("300418", "688981", "920045", "834021", "002297", "000636"):
        assert detect_momentum(BarContext(sym, bars)) is not None, sym


def test_ultra_short_liquidity_amplitude_gates() -> None:
    # 成交额不足 3 亿
    specs = _continuation_specs()
    o, h, lo, c, _to = specs[-1]
    specs[-1] = (o, h, lo, c, 1e8)
    assert detect_momentum(BarContext("300418", _spec_bars(specs))) is None
    # 振幅不足 5%
    specs = _continuation_specs()
    o, h, lo, c, to = specs[-1]
    prev = specs[-2][3]
    tight = prev * 0.02
    specs[-1] = (o, c + tight, c - tight, c, to)
    assert detect_momentum(BarContext("300418", _spec_bars(specs))) is None


def test_ultra_short_breadth_gate() -> None:
    bars = _spec_bars(_continuation_specs())
    assert detect_momentum(BarContext("300418", bars, up_ratio=0.20)) is None
    assert detect_momentum(BarContext("300418", bars, up_ratio=0.60)) is not None
    # up_ratio<0 表示宽度未知，跳过闸门
    assert detect_momentum(BarContext("300418", bars, up_ratio=-1.0)) is not None


def test_ultra_short_no_falling_knife() -> None:
    specs = _pullback_specs()
    o, h, lo, c, to = specs[-1]
    prev = specs[-2][3]
    knife = prev * 0.92  # 当日 -8%，超过 -7% 飞刀阈值
    specs[-1] = (prev, prev * 1.005, knife * 0.98, knife, to)
    assert detect_momentum(BarContext("300684", _spec_bars(specs))) is None


def test_ultra_short_incomplete_data_no_signal() -> None:
    bars = _spec_bars(_continuation_specs())[:15]  # 不足 21 根
    assert detect_momentum(BarContext("300418", bars)) is None


def test_ultra_short_synthetic_backtest_deterministic() -> None:
    from src.strategy_lab.gates import evaluate_gates
    from src.strategy_lab.synthetic import universe_for

    series = universe_for("ultra_short_momentum", n_symbols=20)
    r1 = run_backtest(series, "ultra_short_momentum", hold_days=2)
    r2 = run_backtest(series, "ultra_short_momentum", hold_days=2)
    assert r1.n == r2.n
    assert r1.n >= 200
    assert [f.pnl_pct for f in r1.fills] == [f.pnl_pct for f in r2.fills]
    # 合成样本只证明引擎能跑到 G-bt-2，不算研究过关
    assert any(g.name == "G-bt-2" and g.ok for g in evaluate_gates(r1))


def test_ultra_short_persona_enabled_without_daily_pipeline() -> None:
    """人格 enabled:true 进调度器枚举（hard_scan/复审），但 daily_pipeline:false 禁止 LLM 管线开仓。"""
    from src.agents.cio.trading_persona import list_personas, load_persona_by_id

    personas = {p["id"]: p for p in list_personas()}
    assert "ultra_short_momentum_v1" in personas
    assert personas["ultra_short_momentum_v1"].get("daily_pipeline") is False
    assert "short_term_hot_rotation_v1" in personas
    persona = load_persona_by_id("ultra_short_momentum_v1")
    assert persona.id == "ultra_short_momentum_v1"
    assert persona.max_total_position_for("panic") == 0.0
    assert "ultra_short_momentum" in persona.strategy_regime_compatibility
