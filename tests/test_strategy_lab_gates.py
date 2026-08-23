from src.strategy_lab.engine import BacktestReport
from src.strategy_lab.gates import evaluate_gates, gates_passed


def _rep(**kwargs) -> BacktestReport:
    base = dict(
        strategy_id="hot_sector_pullback",
        n=200, wins=100, losses=100,
        win_rate=0.50, profit_factor=1.3, avg_pnl_pct=0.2,
        max_drawdown_pct=-10.0, oos_n=40, oos_win_rate=0.48,
    )
    base.update(kwargs)
    return BacktestReport(**base)


def test_short_gates_pass() -> None:
    assert gates_passed(_rep())


def test_short_gates_fail_n() -> None:
    r = _rep(n=50, wins=25, losses=25)
    names = {g.name: g.ok for g in evaluate_gates(r)}
    assert names["G-bt-2"] is False
    assert not gates_passed(r)


def test_value_needs_higher_wr() -> None:
    r = _rep(strategy_id="value_investing", win_rate=0.50, oos_win_rate=0.50)
    names = {g.name: g.ok for g in evaluate_gates(r)}
    assert names["G-bt-3"] is False
