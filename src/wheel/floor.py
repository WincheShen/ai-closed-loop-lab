"""SystemFloor — 不可配置的系统底线检查（纯函数）。

任何转盘、任何角色都不能绕过；ExecutionEngine 仍做兜底校验。
"""
from __future__ import annotations

from datetime import datetime

from src.infra.trading_calendar import is_trading_day


def check_trading_day(now: datetime) -> str | None:
    """非交易日返回原因，否则 None。"""
    if not is_trading_day(now.date()):
        return f"{now.date().isoformat()} 非交易日"
    return None


def check_t1(position: dict, now: datetime) -> str | None:
    """当日买入持仓当日不可卖（T+1）。返回原因或 None。"""
    entry_date = position.get("entry_date") or ""
    if entry_date and entry_date == now.date().isoformat():
        return f"当日建仓 (entry_date={entry_date})，受 T+1 限制不可卖出"
    return None


def check_real_quote(quote: dict | None) -> str | None:
    """无真实行情返回原因，否则 None。"""
    if not quote:
        return "无行情数据"
    if quote.get("is_mock"):
        return "行情为 mock 数据，禁止交易动作"
    price = float(quote.get("price") or 0)
    if price <= 0:
        return f"行情价格异常 ({price})"
    return None
