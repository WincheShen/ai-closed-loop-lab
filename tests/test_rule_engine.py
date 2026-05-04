"""规则引擎与内置规则 smoke test。"""
from __future__ import annotations

from pathlib import Path

from stock_analyzer.data_source.akshare_client import StockQuote
from stock_analyzer.rules import (
    Rule,
    RuleEngine,
    load_rules_from_yaml,
    register_builtin_rules,
)
from stock_analyzer.rules import builtin  # noqa: F401


def _stock(**kw) -> StockQuote:
    base = dict(
        symbol="600519", name="贵州茅台", price=1500.0,
        change_pct=4.5, volume=1e6, turnover=2e9,
        turnover_rate=6.0, pe_ttm=25.0, pb=8.0,
        market_cap_yi=2000.0, industry="白酒",
        main_fund_net_inflow=1e8,
    )
    base.update(kw)
    return StockQuote(**base)


def test_volume_breakout_rule():
    register_builtin_rules()
    rule_func = builtin.volume_breakout
    s = _stock(change_pct=5.0, turnover_rate=8.0)
    assert rule_func(s, {"min_change_pct": 3, "min_turnover_rate": 5}) is True

    s2 = _stock(change_pct=1.0, turnover_rate=8.0)
    assert rule_func(s2, {"min_change_pct": 3, "min_turnover_rate": 5}) is False


def test_in_hot_sector_with_dynamic_params():
    s = _stock(industry="低空经济")
    assert builtin.in_hot_sector(s, {"hot_sectors": ["低空经济", "AI算力"]}) is True
    assert builtin.in_hot_sector(s, {"hot_sectors": ["白酒"]}) is False
    assert builtin.in_hot_sector(s, {"hot_sectors": []}) is False


def test_load_yaml_and_filter():
    yaml_path = Path(__file__).resolve().parent.parent / "config" / "rules.yaml"
    rules = load_rules_from_yaml(yaml_path)
    # 给热门板块规则注入参数
    for r in rules:
        if r.id == "in_hot_sector":
            r.params["hot_sectors"] = ["白酒"]

    engine = RuleEngine(rules)
    s = _stock()
    res = engine.evaluate(s)
    assert res.score > 0
    assert "not_st" in res.matched_rule_ids
