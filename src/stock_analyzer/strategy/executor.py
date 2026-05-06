"""策略执行器 — 根据 StrategySpec 从 AKShare 获取全市场数据并过滤。

流程：
    1. 获取全市场行情快照 (AkshareClient)
    2. 按 filters 逐条过滤
    3. 按 rankings 加权排序
    4. 返回 Top N 结果
"""
from __future__ import annotations

import logging
import operator
from dataclasses import dataclass, field
from typing import Any

from ..data_source import AkshareClient
from ..data_source.akshare_client import MarketSnapshot, StockQuote
from .compiler import FilterCondition, RankingPreference, StrategySpec

logger = logging.getLogger(__name__)


@dataclass
class PickResult:
    """单只股票的选股结果。"""
    symbol: str
    name: str
    price: float
    change_pct: float
    industry: str
    pe_ttm: float | None = None
    pb: float | None = None
    market_cap_yi: float | None = None
    turnover_rate: float = 0.0
    main_fund_net_inflow: float = 0.0
    score: float = 0.0
    matched_filters: list[str] = field(default_factory=list)
    failed_filters: list[str] = field(default_factory=list)
    rank_detail: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "price": self.price,
            "change_pct": self.change_pct,
            "industry": self.industry,
            "pe_ttm": self.pe_ttm,
            "pb": self.pb,
            "market_cap_yi": self.market_cap_yi,
            "turnover_rate": self.turnover_rate,
            "main_fund_net_inflow": round(self.main_fund_net_inflow / 1e4, 2),  # 万元
            "score": round(self.score, 3),
            "matched_filters": self.matched_filters,
            "failed_filters": self.failed_filters,
        }


# ---------------------------------------------------------------------------
# Operator mapping
# ---------------------------------------------------------------------------

_OPS = {
    "<=": operator.le,
    ">=": operator.ge,
    "<": operator.lt,
    ">": operator.gt,
    "==": operator.eq,
    "!=": operator.ne,
}


def _get_field_value(stock: StockQuote, field_name: str) -> Any:
    """从 StockQuote 获取指定字段的值。"""
    mapping = {
        "pe_ttm": stock.pe_ttm,
        "pb": stock.pb,
        "market_cap_yi": stock.market_cap_yi,
        "change_pct": stock.change_pct,
        "turnover_rate": stock.turnover_rate,
        "volume": stock.volume,
        "turnover": stock.turnover,
        "main_fund_net_inflow": stock.main_fund_net_inflow,
        "industry": stock.industry,
        "name": stock.name,
        "symbol": stock.symbol,
        "price": stock.price,
    }
    return mapping.get(field_name)


def _check_filter(stock: StockQuote, f: FilterCondition) -> bool:
    """检查单只股票是否满足一个过滤条件。"""
    val = _get_field_value(stock, f.field)

    if f.op == "in":
        # value 是列表，field 值需在列表中
        if isinstance(f.value, list):
            return val in f.value
        return False

    if f.op == "not_in":
        if isinstance(f.value, list):
            return val not in f.value
        return True

    if f.op == "contains":
        # 字符串包含检查
        if val is None:
            return False
        return str(f.value) in str(val)

    if f.op == "between":
        # value 应该是 [min, max]
        if not isinstance(f.value, (list, tuple)) or len(f.value) != 2:
            return False
        if val is None:
            return False
        return f.value[0] <= val <= f.value[1]

    # 数值比较
    if val is None:
        return False
    op_func = _OPS.get(f.op)
    if op_func is None:
        logger.warning("Unknown operator: %s", f.op)
        return False
    try:
        return op_func(float(val), float(f.value))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class StrategyExecutor:
    """根据 StrategySpec 执行选股。"""

    def __init__(self, allow_mock: bool = True):
        self.akshare_client = AkshareClient(allow_mock_fallback=allow_mock)

    def execute(self, spec: StrategySpec, snapshot: MarketSnapshot | None = None) -> list[PickResult]:
        """执行策略，返回排序后的选股结果。

        Args:
            spec: 编译后的策略规格
            snapshot: 可选的市场快照（None 则实时拉取）

        Returns:
            符合条件的股票列表，按评分排序
        """
        if snapshot is None:
            snapshot = self.akshare_client.fetch_snapshot()

        logger.info(
            "Executing strategy '%s' against %d stocks (mock=%s)",
            spec.name, len(snapshot.stocks), snapshot.is_mock,
        )

        # 1. 过滤
        passed: list[tuple[StockQuote, list[str], list[str]]] = []
        for stock in snapshot.stocks:
            # 跳过 ST
            if stock.is_st:
                continue

            matched = []
            failed = []
            all_pass = True

            for f in spec.filters:
                if _check_filter(stock, f):
                    matched.append(f.description or f"{f.field} {f.op} {f.value}")
                else:
                    failed.append(f.description or f"{f.field} {f.op} {f.value}")
                    all_pass = False

            if all_pass:
                passed.append((stock, matched, failed))

        logger.info("Filter pass: %d / %d", len(passed), len(snapshot.stocks))

        # 2. 排序（加权评分）
        results: list[PickResult] = []
        for stock, matched, failed in passed:
            score = 0.0
            rank_detail = {}

            for rank in spec.rankings:
                val = _get_field_value(stock, rank.field)
                if val is None:
                    continue
                try:
                    numeric_val = float(val)
                except (TypeError, ValueError):
                    continue

                # 归一化：简单用原始值 * weight（方向处理）
                if rank.direction == "desc":
                    contribution = numeric_val * rank.weight
                else:
                    # asc: 值越小越好，取负
                    contribution = -numeric_val * rank.weight

                score += contribution
                rank_detail[rank.field] = numeric_val

            results.append(PickResult(
                symbol=stock.symbol,
                name=stock.name,
                price=stock.price,
                change_pct=stock.change_pct,
                industry=stock.industry,
                pe_ttm=stock.pe_ttm,
                pb=stock.pb,
                market_cap_yi=stock.market_cap_yi,
                turnover_rate=stock.turnover_rate,
                main_fund_net_inflow=stock.main_fund_net_inflow,
                score=score,
                matched_filters=matched,
                failed_filters=failed,
                rank_detail=rank_detail,
            ))

        # 排序
        results.sort(key=lambda r: r.score, reverse=True)

        # 限制数量
        results = results[: spec.limit]

        logger.info("Strategy '%s' produced %d picks", spec.name, len(results))
        return results
