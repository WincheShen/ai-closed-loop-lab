"""平仓记录有效性判定 — 统计口径清洗用，不修改原始记录。

识别三类不可能在真实市场发生的成交：
- holiday_entry / holiday_exit：入场或平仓日为休市日（日历缺失期间的模拟成交）
- cost_price_exit：平仓价恰等于成本价且盈亏为 0（强平拿不到行情时按成本价伪造）
- implausible_price：持仓期内按板块涨跌停幅度不可能走到的价格（坏行情数据）
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from src.infra.trading_calendar import is_trading_day, trading_days_between

FLAG_LABELS = {
    "holiday_entry": "休市日入场",
    "holiday_exit": "休市日平仓",
    "cost_price_exit": "按成本价伪平仓",
    "implausible_price": "价格超出涨跌停可能范围",
}


def _parse(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def _daily_limit(symbol: str) -> float:
    s = str(symbol or "")
    if s.startswith(("300", "301", "688", "689")):
        return 0.20
    if s.startswith(("8", "4", "92")):
        return 0.30
    return 0.10


def _max_price_ratio(symbol: str, trading_days_held: int) -> float:
    """入场到平仓价格比的理论上限：当日最多从跌停到涨停，之后每日一个涨跌停。"""
    lim = _daily_limit(symbol)
    return (1 + lim) / (1 - lim) * (1 + lim) ** max(trading_days_held, 0)


def position_validity_flags(pos: dict) -> list[str]:
    flags: list[str] = []
    entry_d = _parse(pos.get("entry_date"))
    close_d = _parse(pos.get("closed_at"))
    if entry_d and not is_trading_day(entry_d):
        flags.append("holiday_entry")
    if close_d and not is_trading_day(close_d):
        flags.append("holiday_exit")

    entry = float(pos.get("entry_price") or 0)
    close = float(pos.get("close_price") or 0)
    if entry > 0 and close > 0:
        if close == entry and not (pos.get("realized_pnl") or 0):
            flags.append("cost_price_exit")
        held = trading_days_between(entry_d, close_d) if entry_d and close_d else 0
        ratio = max(entry, close) / min(entry, close)
        if ratio > _max_price_ratio(pos.get("symbol", ""), held) + 1e-9:
            flags.append("implausible_price")
    return flags


def summarize_closed(rows: Iterable[dict]) -> dict[str, Any]:
    """返回 raw 与 clean 两套口径的胜率/盈亏。"""
    def _agg(items: list[dict]) -> dict[str, Any]:
        n = len(items)
        wins = sum(1 for r in items if (r.get("realized_pnl") or 0) > 0)
        losses = sum(1 for r in items if (r.get("realized_pnl") or 0) < 0)
        return {
            "closed_trades": n,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / n, 4) if n else None,
            "realized_pnl": round(sum(r.get("realized_pnl") or 0 for r in items), 2),
        }

    rows = list(rows)
    clean, excluded = [], []
    for r in rows:
        flags = position_validity_flags(r)
        (excluded if flags else clean).append(r)
        if flags:
            r["validity_flags"] = flags
    return {
        "raw": _agg(rows),
        "clean": _agg(clean),
        "excluded": [
            {
                "position_id": r.get("position_id"),
                "symbol": r.get("symbol"),
                "entry_date": r.get("entry_date"),
                "closed_at": r.get("closed_at"),
                "realized_pnl": r.get("realized_pnl"),
                "flags": r["validity_flags"],
            }
            for r in excluded
        ],
    }
