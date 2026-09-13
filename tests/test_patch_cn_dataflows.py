"""Unit tests for resilient cn_stock dataflow patches."""
import pandas as pd
import pytest

from trading_agent_service.analysis.patch_cn_dataflows import (
    _clean_dataframe,
    resilient_get_fundamentals,
    resilient_get_stock_data,
    resilient_load_ohlcv,
    to_akshare_code,
)


def test_to_akshare_code():
    assert to_akshare_code("600519") == "600519"
    assert to_akshare_code("600519.SS") == "600519"
    assert to_akshare_code("000858.SZ") == "000858"
    assert to_akshare_code("sh600519") == "600519"
    assert to_akshare_code("sz000858") == "000858"


def test_clean_dataframe():
    df = pd.DataFrame({
        "Date": ["2026-01-01", "invalid", "2026-01-03"],
        "Open": [10.0, 11.0, 12.0],
        "High": [11.0, 12.0, 13.0],
        "Low": [9.0, 10.0, 11.0],
        "Close": [10.5, None, 12.5],
        "Volume": [1000, 2000, 3000],
    })
    cleaned = _clean_dataframe(df)
    assert len(cleaned) == 2
    assert "Date" in cleaned.columns
    assert pd.api.types.is_datetime64_any_dtype(cleaned["Date"])


def test_resilient_load_ohlcv_tencent_fallback():
    # 603195 (Gongniu Group) - should load via market-info-platform or Tencent
    df = resilient_load_ohlcv("603195", "2026-09-07")
    assert not df.empty
    assert len(df) >= 30
    assert set(["Date", "Open", "High", "Low", "Close", "Volume"]).issubset(df.columns)


def test_resilient_get_stock_data_fallback():
    data = resilient_get_stock_data("603195", "2026-08-01", "2026-09-07")
    assert "Stock data for 603195" in data
    assert "Date,Open,High,Low,Close,Volume" in data


def test_resilient_get_fundamentals_fallback():
    data = resilient_get_fundamentals("603195")
    assert "Company Fundamentals for 603195" in data
    assert "公牛集团" in data or "603195" in data
    assert "最新价" in data or "总市值" in data
