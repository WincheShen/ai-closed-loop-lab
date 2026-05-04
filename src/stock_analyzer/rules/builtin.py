"""内置选股规则。

每条规则签名： (stock: StockQuote, params: dict) -> bool

注册后即可在 config/rules.yaml 中通过 id 引用。
"""
from __future__ import annotations

from ..data_source.akshare_client import StockQuote
from .rule_engine import register


# ---------------------------------------------------------------------------
# 量价类
# ---------------------------------------------------------------------------

@register("volume_breakout")
def volume_breakout(stock: StockQuote, params: dict) -> bool:
    """放量上涨：当日涨幅 ≥ 阈值 且 换手率 ≥ 阈值。"""
    min_change = float(params.get("min_change_pct", 3.0))
    min_turnover_rate = float(params.get("min_turnover_rate", 5.0))
    return stock.change_pct >= min_change and stock.turnover_rate >= min_turnover_rate


@register("strong_turnover")
def strong_turnover(stock: StockQuote, params: dict) -> bool:
    """成交额活跃：单日成交额 ≥ 阈值（亿元）。"""
    min_turnover_yi = float(params.get("min_turnover_yi", 5.0))
    return stock.turnover >= min_turnover_yi * 1e8


# ---------------------------------------------------------------------------
# 资金类
# ---------------------------------------------------------------------------

@register("main_fund_inflow")
def main_fund_inflow(stock: StockQuote, params: dict) -> bool:
    """主力资金净流入 ≥ 阈值（万元）。"""
    min_inflow_wan = float(params.get("min_inflow_wan", 5000))
    return stock.main_fund_net_inflow >= min_inflow_wan * 1e4


# ---------------------------------------------------------------------------
# 估值类
# ---------------------------------------------------------------------------

@register("reasonable_valuation")
def reasonable_valuation(stock: StockQuote, params: dict) -> bool:
    """估值合理：PE 在区间内 且 PB 不过高。"""
    pe_min = float(params.get("pe_min", 0))
    pe_max = float(params.get("pe_max", 80))
    pb_max = float(params.get("pb_max", 10))
    if stock.pe_ttm is None or stock.pb is None:
        return False
    return pe_min <= stock.pe_ttm <= pe_max and stock.pb <= pb_max


# ---------------------------------------------------------------------------
# 排除类（注意：在外部 stock_filter 处理；此处规则是"加分"逻辑）
# ---------------------------------------------------------------------------

@register("not_st")
def not_st(stock: StockQuote, params: dict) -> bool:
    """非 ST。"""
    return not stock.is_st


@register("market_cap_range")
def market_cap_range(stock: StockQuote, params: dict) -> bool:
    """市值在区间内（亿元）。"""
    min_yi = float(params.get("min_yi", 50))
    max_yi = float(params.get("max_yi", 5000))
    if stock.market_cap_yi is None:
        return False
    return min_yi <= stock.market_cap_yi <= max_yi


@register("in_hot_sector")
def in_hot_sector(stock: StockQuote, params: dict) -> bool:
    """所属行业在热门板块列表中。

    params:
        hot_sectors: list[str]   动态注入的当日热门板块名
    """
    hot = params.get("hot_sectors") or []
    if not hot:
        return False
    return any(h in stock.industry or stock.industry in h for h in hot)


# 便于外部一次性导入
BUILTIN_RULES = [
    "volume_breakout",
    "strong_turnover",
    "main_fund_inflow",
    "reasonable_valuation",
    "not_st",
    "market_cap_range",
    "in_hot_sector",
]


def register_builtin_rules() -> list[str]:
    """已通过装饰器自动注册；此函数仅返回规则 id 列表用于校验。"""
    return list(BUILTIN_RULES)
