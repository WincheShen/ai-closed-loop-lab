"""日线 parquet 缓存。真实拉取走 AkshareClient，失败不写 mock。"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from src.stock_analyzer.data_source.akshare_client import KlineBar

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = _PROJECT_ROOT / "data" / "strategy_lab" / "klines"


def cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol}.json"


def bars_to_records(bars: list[KlineBar]) -> list[dict]:
    return [
        {
            "date": str(b.date),
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
            "turnover": b.turnover,
            "turnover_rate": getattr(b, "turnover_rate", 0.0),
            "change_pct": getattr(b, "change_pct", 0.0),
        }
        for b in bars
    ]


def records_to_bars(rows: list[dict]) -> list[KlineBar]:
    out: list[KlineBar] = []
    for r in rows:
        d = r["date"]
        if isinstance(d, str):
            d = date.fromisoformat(d[:10])
        out.append(
            KlineBar(
                date=d,
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=float(r.get("volume") or 0),
                turnover=float(r.get("turnover") or 0),
                turnover_rate=float(r.get("turnover_rate") or 0),
                change_pct=float(r.get("change_pct") or 0),
            )
        )
    out.sort(key=lambda b: b.date)
    return out


def load_cached(symbol: str) -> list[KlineBar]:
    path = cache_path(symbol)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return records_to_bars(data.get("bars") or [])


def cached_symbols() -> list[str]:
    """本地已缓存日线的全部标的（全池回测，避免手挑股票池的幸存者偏差）。"""
    return sorted(p.stem for p in CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else []


def save_cached(symbol: str, bars: list[KlineBar]) -> Path:
    path = cache_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": symbol,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "bars": bars_to_records(bars),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def fetch_or_load(
    symbol: str,
    *,
    days: int = 520,
    refresh: bool = False,
) -> list[KlineBar]:
    if not refresh:
        cached = load_cached(symbol)
        if cached:
            return cached
    from src.stock_analyzer.data_source.akshare_client import AkshareClient

    client = AkshareClient(allow_mock_fallback=False)
    bars = client.fetch_kline(symbol, days=days)
    if bars:
        save_cached(symbol, bars)
    return bars


DEFAULT_UNIVERSE = {
    "hot_sector_pullback": [
        "300750", "002475", "002371", "603501", "688981",
        "300124", "002415", "000725", "002230", "300274",
    ],
    "bottom_reversal": [
        "000001", "600036", "601318", "600519", "000858",
        "601166", "600276", "000333", "002142", "601888",
    ],
    "value_investing": [
        "600519", "000858", "601318", "600036", "000333",
        "600900", "601166", "000001", "600276", "601888",
        "600309", "000568", "600887", "002304", "601012",
    ],
    "ultra_short_momentum": [
        "300418", "301171", "300684", "688143", "000636",
        "002297", "300209", "300750", "002475", "300124",
        "002230", "300274", "300782", "688981",
    ],
}
