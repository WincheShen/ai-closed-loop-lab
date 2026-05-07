"""技术指标计算 — 基于历史K线数据筛选技术面条件。

支持的技术面条件（来自 StrategySpec.technicals 字段解析）：
- new_high: 近期创新高（N日内的最高价）
- pullback: 创新高后正在回调（当前价低于近期高点 X%）
- above_ma: 价格在 MA5/MA10/MA20/MA60 之上
- below_ma: 价格在 MA 之下
- volume_surge: 放量（成交量/成交额高于 N 日均量的倍数）
- turnover_min: 换手率高于阈值
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..data_source.akshare_client import KlineBar

logger = logging.getLogger(__name__)


@dataclass
class TechnicalResult:
    """单只股票技术面检查结果。"""
    symbol: str
    passed: bool
    details: dict = field(default_factory=dict)


def _ma(bars: list[KlineBar], n: int) -> Optional[float]:
    """计算最近 n 根 close 的均线。"""
    closes = [b.close for b in bars[-n:] if b.close > 0]
    if len(closes) < n:
        return None
    return sum(closes) / len(closes)


def _recent_high(bars: list[KlineBar], n: int) -> float:
    """最近 n 根 K 线的最高价。"""
    highs = [b.high for b in bars[-n:]]
    return max(highs) if highs else 0.0


def _is_new_high(bars: list[KlineBar], lookback: int = 20) -> bool:
    """最近 lookback 日内出现过新高（且高点在最近5日内）。"""
    if len(bars) < lookback + 1:
        return False
    past_high = _recent_high(bars[:-5], lookback - 5)
    recent_high = _recent_high(bars[-5:], 5)
    return recent_high > past_high


def _is_pullback(bars: list[KlineBar], lookback: int = 20, max_pullback_pct: float = 10.0) -> bool:
    """创新高后正在回调：
    - 近 lookback 日内有高点
    - 当前价格低于高点但回调幅度不超过 max_pullback_pct%
    """
    if len(bars) < lookback:
        return False
    recent_high = _recent_high(bars[-lookback:], lookback)
    current = bars[-1].close
    if recent_high <= 0 or current <= 0:
        return False
    pullback_pct = (recent_high - current) / recent_high * 100
    return 0 < pullback_pct <= max_pullback_pct


def _above_ma(bars: list[KlineBar], n: int) -> bool:
    """当前收盘价站上 MA-n。"""
    if len(bars) < n:
        return False
    ma = _ma(bars, n)
    if ma is None:
        return False
    return bars[-1].close >= ma


def _volume_surge(bars: list[KlineBar], n_avg: int = 20, min_amount_yi: float = 5.0) -> bool:
    """成交额高于 N 日均值且高于阈值（亿元）。"""
    if len(bars) < n_avg + 1:
        return False
    avg_turnover = sum(b.turnover for b in bars[-(n_avg + 1):-1]) / n_avg
    recent_max = max(b.turnover for b in bars[-5:])
    threshold = min_amount_yi * 1e8
    return recent_max >= threshold and recent_max >= avg_turnover


def check_technicals(
    symbol: str,
    bars: list[KlineBar],
    conditions: list[str],
) -> TechnicalResult:
    """根据 technicals 描述列表检查股票是否满足技术面要求。

    conditions 解析规则（从 LLM 生成的 technicals 字段提取关键词）:
        - 包含"创新高" → 检查 new_high
        - 包含"回调" → 检查 pullback
        - 包含"5日均线" 或 "MA5" → 检查 above_ma(5)
        - 包含"没破" + "均线" → 检查 above_ma
        - 包含"放量" 或 "成交" + "亿" → 检查 volume_surge
    """
    if not bars:
        return TechnicalResult(symbol=symbol, passed=False, details={"error": "no kline data"})

    details = {}
    checks: list[bool] = []

    joined = " ".join(conditions)

    if "创新高" in joined or "新高" in joined:
        r = _is_new_high(bars, lookback=20)
        details["new_high_20d"] = r
        checks.append(r)

    if "回调" in joined:
        r = _is_pullback(bars, lookback=20, max_pullback_pct=10.0)
        details["pullback_within_10pct"] = r
        checks.append(r)

    if "5日均线" in joined or "MA5" in joined or "ma5" in joined or "5均" in joined:
        r = _above_ma(bars, 5)
        details["above_ma5"] = r
        checks.append(r)

    if "10日均线" in joined or "MA10" in joined or "10均" in joined:
        r = _above_ma(bars, 10)
        details["above_ma10"] = r
        checks.append(r)

    if "20日均线" in joined or "MA20" in joined or "20均" in joined:
        r = _above_ma(bars, 20)
        details["above_ma20"] = r
        checks.append(r)

    if ("没破" in joined or "未破" in joined or "站上" in joined) and "均线" in joined:
        r = _above_ma(bars, 5)
        details["above_ma5_not_broken"] = r
        checks.append(r)

    if "放量" in joined or ("成交" in joined and "亿" in joined):
        r = _volume_surge(bars, n_avg=20, min_amount_yi=5.0)
        details["volume_surge_5yi"] = r
        checks.append(r)

    if not checks:
        return TechnicalResult(symbol=symbol, passed=True, details={"note": "no parseable conditions"})

    return TechnicalResult(symbol=symbol, passed=all(checks), details=details)
