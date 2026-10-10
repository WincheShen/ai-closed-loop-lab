"""A 股交易日历（沪深交易所休市日）。

只维护"落在工作日的休市日"；周末默认休市。调休补班的周六/周日交易所
同样不开市，因此无需额外处理。

数据来源：
- 2026：由 NAS market-info(:8005) 腾讯日线反推（工作日缺失 K 线 = 休市），
  与国务院 2026 年放假安排一致。
- 2025：国务院 2025 年放假安排。

每年 12 月国务院公布次年安排后，需补充到 _HOLIDAYS。未覆盖年份按
"工作日即交易日"降级处理，并打 warning，避免静默错误。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

_HOLIDAYS: dict[int, frozenset[str]] = {
    2025: frozenset({
        "2025-01-01",
        "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
        "2025-02-03", "2025-02-04",
        "2025-04-04",
        "2025-05-01", "2025-05-02", "2025-05-05",
        "2025-06-02",
        "2025-10-01", "2025-10-02", "2025-10-03",
        "2025-10-06", "2025-10-07", "2025-10-08",
    }),
    2026: frozenset({
        "2026-01-01", "2026-01-02",
        "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
        "2026-02-23",
        "2026-04-06",
        "2026-05-01", "2026-05-04", "2026-05-05",
        "2026-06-19",
        "2026-09-25",
        "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
    }),
}

_warned_years: set[int] = set()


def _to_date(d: date | datetime | None) -> date:
    if d is None:
        return date.today()
    return d.date() if isinstance(d, datetime) else d


def is_trading_day(d: date | datetime | None = None) -> bool:
    """是否为 A 股交易日（周末与法定休市日返回 False）。"""
    d = _to_date(d)
    if d.weekday() >= 5:
        return False
    holidays = _HOLIDAYS.get(d.year)
    if holidays is None:
        if d.year not in _warned_years:
            _warned_years.add(d.year)
            logger.warning(
                "trading_calendar 未覆盖 %d 年休市日，按工作日处理 — 请补充 _HOLIDAYS", d.year,
            )
        return True
    return d.isoformat() not in holidays


def trading_days_between(start: date, end: date) -> int:
    """start 之后到 end（含）的交易日数。"""
    if end <= start:
        return 0
    n = 0
    cur = start + timedelta(days=1)
    while cur <= end:
        if is_trading_day(cur):
            n += 1
        cur += timedelta(days=1)
    return n


def previous_trading_day(d: date | None = None) -> date:
    """d 之前最近的一个交易日。"""
    cur = _to_date(d) - timedelta(days=1)
    while not is_trading_day(cur):
        cur -= timedelta(days=1)
    return cur
