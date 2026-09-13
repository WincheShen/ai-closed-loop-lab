"""Resilient dataflow fallbacks for Chinese A-share data in tradingagents.

Resolves RemoteDisconnected / connection aborts from Eastmoney (push2his/push2)
by providing multi-tier fallbacks:
1. market-information-platform (via MARKET_INFO_URL, e.g. http://192.168.3.73:8005)
2. Tencent K-line & Quote APIs (high-availability, reliable)
3. Sina K-line API
4. Original AKShare calls with graceful degradation
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from datetime import datetime
from typing import Annotated

import pandas as pd

logger = logging.getLogger(__name__)

_APPLIED = False


def to_akshare_code(symbol: str) -> str:
    """Convert any ticker format to bare 6-digit AKShare code."""
    s = symbol.strip().upper()
    s = re.sub(r"\.(SS|SH|SZ)$", "", s)
    s = re.sub(r"^(SH|SZ)", "", s)
    return s


def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize a stock DataFrame for stockstats: parse dates, drop invalid rows, fill gaps."""
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"])

    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    data = data.dropna(subset=["Close"])
    data[price_cols] = data[price_cols].ffill().bfill()
    return data


def _fetch_from_market_info_platform(code: str) -> pd.DataFrame | None:
    """Fetch daily K-line from market-information-platform if configured."""
    base_url = os.getenv("MARKET_INFO_URL", "").rstrip("/")
    if not base_url:
        return None

    try:
        url = f"{base_url}/api/market/kline/{code}?period=daily"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        bars = payload.get("bars", [])
        if bars and len(bars) >= 5:
            rows = [
                {
                    "Date": b["trade_date"],
                    "Open": float(b["open"]),
                    "High": float(b["high"]),
                    "Low": float(b["low"]),
                    "Close": float(b["close"]),
                    "Volume": float(b["volume"]),
                }
                for b in bars
            ]
            df = pd.DataFrame(rows)
            logger.info("Loaded %d bars for %s from market-information-platform", len(df), code)
            return df
    except Exception as e:
        logger.warning("market-information-platform kline failed for %s: %s", code, e)
    return None


def _fetch_from_tencent_kline(code: str) -> pd.DataFrame | None:
    """Fetch daily forward-adjusted K-line from Tencent."""
    try:
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,320,qfq"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        data_dict = payload.get("data", {}).get(f"{prefix}{code}", {})
        raw_bars = data_dict.get("qfqday") or data_dict.get("day") or []
        if raw_bars and len(raw_bars) >= 5:
            rows = []
            for b in raw_bars:
                if isinstance(b, (list, tuple)) and len(b) >= 6:
                    rows.append({
                        "Date": str(b[0]),
                        "Open": float(b[1]),
                        "High": float(b[3]),
                        "Low": float(b[4]),
                        "Close": float(b[2]),
                        "Volume": float(b[5]),
                    })
            df = pd.DataFrame(rows)
            logger.info("Loaded %d bars for %s from Tencent K-line", len(df), code)
            return df
    except Exception as e:
        logger.warning("Tencent kline fallback failed for %s: %s", code, e)
    return None


def _fetch_from_sina_kline(code: str) -> pd.DataFrame | None:
    """Fetch daily K-line from Sina."""
    try:
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        url = (
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen=240"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_rows = json.loads(resp.read().decode("utf-8"))

        if raw_rows and len(raw_rows) >= 5:
            rows = [
                {
                    "Date": str(r.get("day", "")),
                    "Open": float(r.get("open", 0.0)),
                    "High": float(r.get("high", 0.0)),
                    "Low": float(r.get("low", 0.0)),
                    "Close": float(r.get("close", 0.0)),
                    "Volume": float(r.get("volume", 0.0)),
                }
                for r in raw_rows
            ]
            df = pd.DataFrame(rows)
            logger.info("Loaded %d bars for %s from Sina K-line", len(df), code)
            return df
    except Exception as e:
        logger.warning("Sina kline fallback failed for %s: %s", code, e)
    return None


def resilient_load_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    """Resilient A-share OHLCV loader with disk caching and multi-tier fallbacks."""
    code = to_akshare_code(symbol)
    curr_date_dt = pd.to_datetime(curr_date)

    # 1. Check local CSV cache
    try:
        from tradingagents.dataflows.config import get_config  # type: ignore
        config = get_config()
        cache_dir = config.get("data_cache_dir", "data/cache")
    except Exception:
        cache_dir = "data/cache"

    today_date = pd.Timestamp.today()
    start_date = today_date - pd.DateOffset(years=5)
    start_str = start_date.strftime("%Y%m%d")
    end_str = today_date.strftime("%Y%m%d")

    os.makedirs(cache_dir, exist_ok=True)
    data_file = os.path.join(
        cache_dir,
        f"{code}-AKShare-data-{start_str}-{end_str}.csv",
    )

    if os.path.exists(data_file):
        try:
            data = pd.read_csv(data_file, on_bad_lines="skip")
            if not data.empty and "Date" in data.columns and "Close" in data.columns:
                data = _clean_dataframe(data)
                return data[data["Date"] <= curr_date_dt]
        except Exception:
            pass

    df = None

    # Tier 0: market-information-platform
    df = _fetch_from_market_info_platform(code)

    # Tier 1: Tencent K-line
    if df is None or df.empty:
        df = _fetch_from_tencent_kline(code)

    # Tier 2: Sina K-line
    if df is None or df.empty:
        df = _fetch_from_sina_kline(code)

    # Tier 3: Original akshare (push2his)
    if df is None or df.empty:
        try:
            import akshare as ak  # type: ignore
            raw_df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_str,
                end_date=end_str,
                adjust="qfq",
            )
            if raw_df is not None and not raw_df.empty:
                col_map = {
                    "日期": "Date", "开盘": "Open", "收盘": "Close",
                    "最高": "High", "最低": "Low", "成交量": "Volume",
                }
                raw_df = raw_df.rename(columns=col_map)
                keep = [c for c in ["Date", "Open", "High", "Low", "Close", "Volume"] if c in raw_df.columns]
                df = raw_df[keep]
        except Exception as e:
            logger.warning("ak.stock_zh_a_hist failed for %s: %s", code, e)

    if df is not None and not df.empty:
        try:
            df.to_csv(data_file, index=False)
        except Exception:
            pass
        df = _clean_dataframe(df)
        df = df[df["Date"] <= curr_date_dt]
        return df

    raise RuntimeError(f"Unable to load OHLCV data for {symbol} from any upstream")


def resilient_get_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Retrieve A-share OHLCV stock data via resilient multi-tier fallbacks."""
    code = to_akshare_code(symbol)
    start_fmt = start_date.replace("-", "")
    end_fmt = end_date.replace("-", "")

    # Try akshare first
    try:
        import akshare as ak  # type: ignore
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_fmt,
            end_date=end_fmt,
            adjust="qfq",
        )
        if df is not None and not df.empty:
            col_map = {
                "日期": "Date", "开盘": "Open", "收盘": "Close",
                "最高": "High", "最低": "Low", "成交量": "Volume",
            }
            df = df.rename(columns=col_map)
            keep = [c for c in ["Date", "Open", "High", "Low", "Close", "Volume"] if c in df.columns]
            df = df[keep]
            for col in ["Open", "High", "Low", "Close"]:
                if col in df.columns:
                    df[col] = df[col].round(2)
            csv_string = df.to_csv(index=False)
            header = (
                f"# Stock data for {symbol.upper()} from {start_date} to {end_date}\n"
                f"# Total records: {len(df)}\n"
                "# Data source: AKShare (A-share, forward-adjusted)\n"
                f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            )
            return header + csv_string
    except Exception as e:
        logger.warning("ak.stock_zh_a_hist failed for %s (%s), falling back to resilient loader", symbol, e)

    # Fallback to resilient loader
    try:
        df = resilient_load_ohlcv(symbol, end_date)
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        df = df[(df["Date"] >= start_dt) & (df["Date"] <= end_dt)]
        if df.empty:
            return f"No data found for symbol '{symbol}' between {start_date} and {end_date}"

        df_out = df.copy()
        df_out["Date"] = df_out["Date"].dt.strftime("%Y-%m-%d")
        cols = [c for c in ["Date", "Open", "High", "Low", "Close", "Volume"] if c in df_out.columns]
        df_out = df_out[cols]
        for col in ["Open", "High", "Low", "Close"]:
            if col in df_out.columns:
                df_out[col] = df_out[col].round(2)
        csv_string = df_out.to_csv(index=False)
        header = (
            f"# Stock data for {symbol.upper()} from {start_date} to {end_date}\n"
            f"# Total records: {len(df_out)}\n"
            "# Data source: Resilient Fallback (MarketInfo/Tencent/Sina)\n"
            f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )
        return header + csv_string
    except Exception as exc:
        return f"No data found for symbol '{symbol}' between {start_date} and {end_date}: {exc}"


def resilient_get_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "current date (unused, for API compatibility)"] = None,
) -> str:
    """Get company fundamentals overview from East Money with Tencent Quote fallback."""
    code = to_akshare_code(ticker)

    # 1. Try AKShare East Money
    try:
        import akshare as ak  # type: ignore
        df = ak.stock_individual_info_em(symbol=code)
        if df is not None and not df.empty:
            lines = []
            for _, row in df.iterrows():
                item = row.iloc[0]
                value = row.iloc[1]
                if pd.notna(value) and str(value).strip():
                    lines.append(f"{item}: {value}")
            header = (
                f"# Company Fundamentals for {ticker.upper()} (CAS)\n"
                "# Data source: East Money via AKShare\n"
                f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            )
            return header + "\n".join(lines)
    except Exception as e:
        logger.warning("ak.stock_individual_info_em failed for %s (%s), falling back to Tencent Quote", ticker, e)

    # 2. Fallback to Tencent real-time quote
    try:
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        url = f"http://qt.gtimg.cn/q={prefix}{code}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("gbk", errors="ignore")

        fields = raw.split("~")
        if len(fields) > 50:
            lines = [
                f"股票代码: {fields[2]}",
                f"股票简称: {fields[1]}",
                f"最新价: {fields[3]}",
                f"涨跌幅: {fields[32]}%",
                f"成交量(手): {fields[36]}",
                f"成交额(万): {fields[37]}",
                f"换手率: {fields[38]}%",
                f"市盈率(动): {fields[39]}",
                f"流通市值: {fields[44]}亿元",
                f"总市值: {fields[45]}亿元",
                f"市净率: {fields[46]}",
                f"市盈率(TTM): {fields[52]}",
                f"市盈率(静): {fields[53]}",
            ]
            header = (
                f"# Company Fundamentals for {ticker.upper()} (CAS)\n"
                "# Data source: Tencent Quote (Fallback)\n"
                f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            )
            return header + "\n".join(lines)
    except Exception as exc:
        logger.warning("Tencent quote fallback failed for %s: %s", ticker, exc)

    return f"No fundamentals data found for symbol '{ticker}'"


def apply_dataflow_patches() -> bool:
    """Monkey-patch tradingagents cn_stock dataflows to use resilient fallbacks."""
    global _APPLIED
    if _APPLIED:
        return True

    try:
        import tradingagents.dataflows.cn_stock.akshare_stock as ak_stock  # type: ignore
        import tradingagents.dataflows.cn_stock.akshare_fundamental as ak_fund  # type: ignore
        import tradingagents.dataflows.cn_stock.akshare_indicator as ak_ind  # type: ignore
        import tradingagents.dataflows.interface as iface  # type: ignore

        ak_stock.load_ohlcv_akshare = resilient_load_ohlcv
        ak_stock.get_stock_data = resilient_get_stock_data
        ak_fund.get_fundamentals = resilient_get_fundamentals
        ak_ind.load_ohlcv_akshare = resilient_load_ohlcv

        # Update VENDOR_METHODS routing table
        if "get_stock_data" in iface.VENDOR_METHODS:
            iface.VENDOR_METHODS["get_stock_data"]["akshare"] = resilient_get_stock_data
        if "get_fundamentals" in iface.VENDOR_METHODS:
            iface.VENDOR_METHODS["get_fundamentals"]["akshare"] = resilient_get_fundamentals

        _APPLIED = True
        logger.info("Resilient cn_stock dataflow patches applied to tradingagents")
        return True
    except Exception as e:
        logger.warning("Failed to apply dataflow patches to tradingagents: %s", e)
        return False
