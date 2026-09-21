"""确定性合成行情 — CI / 本地无网也能凑满 200+ 笔。不算作研究闸门过关证据。"""
from __future__ import annotations

from datetime import date, timedelta

from src.stock_analyzer.data_source.akshare_client import KlineBar


def trading_days(n: int, start: date = date(2024, 1, 2)) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bar(d: date, o: float, h: float, l: float, c: float, vol: float = 1e4, to: float = 1e8) -> KlineBar:
    return KlineBar(
        date=d, open=o, high=h, low=l, close=c,
        volume=vol, turnover=to, change_pct=(c / o - 1) * 100 if o else 0,
    )


def pullback_series(symbol: str, cycles: int = 18, start: date = date(2024, 1, 2)) -> list[KlineBar]:
    """每周期：抬高 16 日 + 回撤约 8% 4 日 + 再走 10 日。"""
    days = trading_days(cycles * 30, start)
    bars: list[KlineBar] = []
    price = 10.0 + (hash(symbol) % 7)
    i = 0
    for _ in range(cycles):
        for step in range(16):
            c = price + step * 0.25
            o = c - 0.05
            bars.append(_bar(days[i], o, c * 1.01, o * 0.99, c, vol=12000 + step, to=1.2e8))
            i += 1
        peak = bars[-1].close
        for step in range(4):
            c = peak * (1 - 0.025 * (step + 1))
            o = c + 0.05
            bars.append(_bar(days[i], o, o * 1.005, c * 0.995, c, vol=11000, to=1.1e8))
            i += 1
        last = bars[-1].close
        for step in range(10):
            c = last * (1 + 0.004 * step)
            bars.append(_bar(days[i], c, c * 1.01, c * 0.99, c, vol=10000, to=1e8))
            i += 1
        price = bars[-1].close
    return bars


def bottom_series(symbol: str, cycles: int = 16, start: date = date(2024, 1, 2)) -> list[KlineBar]:
    """连跌后放量阳线并金叉。"""
    days = trading_days(cycles * 24, start)
    bars: list[KlineBar] = []
    price = 20.0 + (hash(symbol) % 5)
    i = 0
    for _ in range(cycles):
        for step in range(12):
            c = price - step * 0.35
            o = c + 0.1
            bars.append(_bar(days[i], o, o, c * 0.99, c, vol=8000, to=8e7))
            i += 1
        # 金叉阳线
        prev = bars[-1].close
        c = prev * 1.04
        bars.append(_bar(days[i], prev * 0.995, c * 1.01, prev * 0.99, c, vol=30000, to=2.4e8))
        i += 1
        for step in range(11):
            c = bars[-1].close * 1.003
            bars.append(_bar(days[i], c, c * 1.01, c * 0.995, c, vol=9000, to=9e7))
            i += 1
        price = bars[-1].close
    return bars


def value_series(symbol: str, days_n: int = 320, start: date = date(2024, 1, 2)) -> list[KlineBar]:
    """多数时间在均线附近、不创新高。"""
    days = trading_days(days_n, start)
    bars: list[KlineBar] = []
    price = 40.0 + (hash(symbol) % 9)
    for idx, d in enumerate(days):
        wave = 0.8 * ((idx % 40) / 40.0 - 0.5)
        c = price * (1 + wave * 0.04)
        o = c * 0.999
        bars.append(_bar(d, o, c * 1.005, c * 0.99, c, vol=15000, to=1.5e8))
    return bars


def momentum_series(symbol: str, cycles: int = 18, start: date = date(2024, 1, 2)) -> list[KlineBar]:
    """每周期：慢涨 15 日 + 放量急涨 3 日（延续形态）+ 宽幅回踩 4 日（回踩形态）+ 过渡 8 日。"""
    days = trading_days(cycles * 30, start)
    bars: list[KlineBar] = []
    price = 20.0 + (hash(symbol) % 9)
    i = 0
    for _ in range(cycles):
        for step in range(15):
            c = price * (1 + 0.008 * step)
            o = c * 0.997
            bars.append(_bar(days[i], o, c * 1.015, o * 0.985, c, vol=20000, to=4e8))
            i += 1
        base = bars[-1].close
        for step in range(3):
            o = base * (1 + 0.035 * step)
            c = base * (1 + 0.035 * (step + 1))
            bars.append(_bar(days[i], o, c * 1.03, o * 0.965, c, vol=60000, to=6e8))
            i += 1
        peak = bars[-1].close
        for step in range(4):
            c = peak * (1 - 0.022 * (step + 1))
            o = c * 0.995
            bars.append(_bar(days[i], o, o * 1.01, c * 0.94, c, vol=30000, to=4e8))
            i += 1
        last = bars[-1].close
        for step in range(8):
            c = last * (1 + 0.003 * step)
            o = c * 0.998
            bars.append(_bar(days[i], o, c * 1.015, o * 0.985, c, vol=18000, to=3.5e8))
            i += 1
        price = bars[-1].close
    return bars


def universe_for(strategy_id: str, n_symbols: int = 20) -> dict[str, list[KlineBar]]:
    if strategy_id == "hot_sector_pullback":
        return {f"P{i:04d}": pullback_series(f"P{i:04d}") for i in range(n_symbols)}
    if strategy_id == "bottom_reversal":
        return {f"B{i:04d}": bottom_series(f"B{i:04d}") for i in range(n_symbols)}
    if strategy_id == "ultra_short_momentum":
        # 合成代码也需落在检测器允许的创业板前缀内
        return {f"300{i:03d}": momentum_series(f"300{i:03d}") for i in range(n_symbols)}
    return {f"V{i:04d}": value_series(f"V{i:04d}") for i in range(n_symbols)}
