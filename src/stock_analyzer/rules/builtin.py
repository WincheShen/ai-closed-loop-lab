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
    if not hot or not stock.industry:
        return False
    return any(
        (len(h) >= 2 and h in stock.industry) or (len(stock.industry) >= 2 and stock.industry in h)
        for h in hot
    )


@register("institutional_buying")
def institutional_buying(stock: StockQuote, params: dict) -> bool:
    """近期机构龙虎榜净买入（"聪明钱"信号）。

    params:
        dragon_tiger_map: dict[str, dict]  动态注入的 {symbol: 龙虎榜聚合记录}
        min_net_buy_wan: float  最小机构净买入（万元），默认 5000
        min_appearance: int     最小上榜次数，默认 1
    """
    dt_map = params.get("dragon_tiger_map") or {}
    if not dt_map:
        return False
    record = dt_map.get(stock.symbol)
    if not record:
        return False

    min_net_buy_wan = float(params.get("min_net_buy_wan", 5000))
    min_appearance = int(params.get("min_appearance", 1))

    inst_net_wan = record.get("institutional_net_buy_wan", 0)
    appearance = record.get("appearance_count", 0)

    return inst_net_wan >= min_net_buy_wan and appearance >= min_appearance


@register("positive_news_catalyst")
def positive_news_catalyst(stock: StockQuote, params: dict) -> bool:
    """近期存在正面新闻催化剂。

    params:
        news_map: dict[str, dict]  动态注入的 {symbol: 新闻聚合记录}
        min_positive: int          最小正面新闻条数（默认 1）
    """
    news_map = params.get("news_map") or {}
    if not news_map:
        return False
    record = news_map.get(stock.symbol)
    if not record:
        return False

    min_positive = int(params.get("min_positive", 1))
    return (
        record.get("has_positive_catalyst", False)
        and record.get("positive_count", 0) >= min_positive
    )


# ---------------------------------------------------------------------------
# 基本面类（价值投资人格使用）
# ---------------------------------------------------------------------------

@register("high_roe")
def high_roe(stock: StockQuote, params: dict) -> bool:
    """ROE ≥ 阈值（%）。"""
    min_roe = float(params.get("min_roe", 15))
    return stock.roe is not None and stock.roe >= min_roe


@register("low_debt")
def low_debt(stock: StockQuote, params: dict) -> bool:
    """资产负债率 ≤ 阈值（0-1 比例）。"""
    max_de = float(params.get("max_de", 0.5))
    return stock.debt_to_equity is not None and stock.debt_to_equity <= max_de


@register("high_dividend")
def high_dividend(stock: StockQuote, params: dict) -> bool:
    """股息率 ≥ 阈值（%）。"""
    min_yield = float(params.get("min_yield", 2.0))
    return stock.dividend_yield is not None and stock.dividend_yield >= min_yield


@register("high_fcf_yield")
def high_fcf_yield(stock: StockQuote, params: dict) -> bool:
    """自由现金流收益率 ≥ 阈值（%）。"""
    min_fcf = float(params.get("min_fcf", 4.0))
    return stock.fcf_yield is not None and stock.fcf_yield >= min_fcf


@register("value_pe")
def value_pe(stock: StockQuote, params: dict) -> bool:
    """价值投资 PE 筛选：PE 在低估区间。"""
    pe_max = float(params.get("pe_max", 30))
    if stock.pe_ttm is None:
        return False
    return 0 < stock.pe_ttm <= pe_max


# 便于外部一次性导入
BUILTIN_RULES = [
    "volume_breakout",
    "strong_turnover",
    "main_fund_inflow",
    "reasonable_valuation",
    "not_st",
    "market_cap_range",
    "in_hot_sector",
    "institutional_buying",
    "positive_news_catalyst",
    "high_roe",
    "low_debt",
    "high_dividend",
    "high_fcf_yield",
    "value_pe",
]


def register_builtin_rules() -> list[str]:
    """已通过装饰器自动注册；此函数仅返回规则 id 列表用于校验。"""
    return list(BUILTIN_RULES)
