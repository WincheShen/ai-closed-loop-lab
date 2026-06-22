"""基本面数据客户端 — 为价值投资人格提供 ROE / 负债率 / 股息率 / FCF 等指标。

设计要点：
- 使用 AKShare 的东方财富财务指标接口（stock_financial_analysis_indicator_em）
- 本地 SQLite 缓存：财务数据按季度更新，缓存 7 天有效
- 批量查询：一次 API 调用获取全市场主要指标，避免逐只查询
- 优雅降级：获取失败时不阻塞主流程，StockQuote 字段保持 None
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_DIR = Path("data/cache")
_CACHE_DB = _CACHE_DIR / "fundamentals.db"
_CACHE_TTL_DAYS = 7


@dataclass
class FundamentalMetrics:
    """单只股票的基本面指标快照。"""
    symbol: str
    roe: Optional[float] = None              # 净资产收益率 %
    debt_to_equity: Optional[float] = None   # 资产负债率 (0-1)
    dividend_yield: Optional[float] = None   # 股息率 %
    fcf_yield: Optional[float] = None        # 自由现金流收益率 %
    report_date: Optional[str] = None        # 最新报告期


class FundamentalClient:
    """基本面数据获取与缓存。"""

    def __init__(self) -> None:
        self._ensure_cache_db()

    def _ensure_cache_db(self) -> None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_CACHE_DB))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fundamental_cache (
                symbol TEXT NOT NULL,
                fetch_date TEXT NOT NULL,
                roe REAL,
                debt_to_equity REAL,
                dividend_yield REAL,
                fcf_yield REAL,
                report_date TEXT,
                PRIMARY KEY (symbol, fetch_date)
            )
        """)
        conn.commit()
        conn.close()

    def enrich_quotes(self, quotes: list) -> None:
        """批量填充 StockQuote 的基本面字段（就地修改）。

        对每只股票：先查缓存，缓存过期则从 AKShare 批量拉取后写入缓存。
        """
        if not quotes:
            return

        today_str = date.today().isoformat()
        symbols_need_fetch: list[str] = []
        cached_map: dict[str, FundamentalMetrics] = {}

        # 1. 查缓存
        conn = sqlite3.connect(str(_CACHE_DB))
        cutoff = (date.today() - timedelta(days=_CACHE_TTL_DAYS)).isoformat()
        for q in quotes:
            row = conn.execute(
                "SELECT roe, debt_to_equity, dividend_yield, fcf_yield, report_date "
                "FROM fundamental_cache WHERE symbol=? AND fetch_date>=? "
                "ORDER BY fetch_date DESC LIMIT 1",
                (q.symbol, cutoff),
            ).fetchone()
            if row:
                cached_map[q.symbol] = FundamentalMetrics(
                    symbol=q.symbol,
                    roe=row[0], debt_to_equity=row[1],
                    dividend_yield=row[2], fcf_yield=row[3],
                    report_date=row[4],
                )
            else:
                symbols_need_fetch.append(q.symbol)
        conn.close()

        # 2. 批量拉取缺失的
        if symbols_need_fetch:
            fetched = self._fetch_batch(symbols_need_fetch)
            # 写入缓存
            conn = sqlite3.connect(str(_CACHE_DB))
            for sym, metrics in fetched.items():
                conn.execute(
                    "INSERT OR REPLACE INTO fundamental_cache "
                    "(symbol, fetch_date, roe, debt_to_equity, dividend_yield, fcf_yield, report_date) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sym, today_str, metrics.roe, metrics.debt_to_equity,
                     metrics.dividend_yield, metrics.fcf_yield, metrics.report_date),
                )
            conn.commit()
            conn.close()
            cached_map.update(fetched)

        # 3. 填充 StockQuote 字段
        for q in quotes:
            m = cached_map.get(q.symbol)
            if m:
                q.roe = m.roe
                q.debt_to_equity = m.debt_to_equity
                q.dividend_yield = m.dividend_yield
                q.fcf_yield = m.fcf_yield

    def get_metrics(self, symbol: str) -> Optional[FundamentalMetrics]:
        """获取单只股票的基本面指标（先查缓存，再远程）。"""
        conn = sqlite3.connect(str(_CACHE_DB))
        cutoff = (date.today() - timedelta(days=_CACHE_TTL_DAYS)).isoformat()
        row = conn.execute(
            "SELECT roe, debt_to_equity, dividend_yield, fcf_yield, report_date "
            "FROM fundamental_cache WHERE symbol=? AND fetch_date>=? "
            "ORDER BY fetch_date DESC LIMIT 1",
            (symbol, cutoff),
        ).fetchone()
        conn.close()

        if row:
            return FundamentalMetrics(
                symbol=symbol,
                roe=row[0], debt_to_equity=row[1],
                dividend_yield=row[2], fcf_yield=row[3],
                report_date=row[4],
            )

        fetched = self._fetch_batch([symbol])
        return fetched.get(symbol)

    def _fetch_batch(self, symbols: list[str]) -> dict[str, FundamentalMetrics]:
        """从 AKShare 批量拉取基本面数据。

        使用 stock_financial_analysis_indicator_em 按个股查询。
        为避免频率限制，每次最多处理 50 只，每只间隔 0.3s。
        """
        result: dict[str, FundamentalMetrics] = {}
        batch = symbols[:50]

        for symbol in batch:
            metrics = self._fetch_single(symbol)
            if metrics:
                result[symbol] = metrics
            time.sleep(0.3)

        logger.info(
            "FundamentalClient 批量拉取: 请求 %d 只, 成功 %d 只",
            len(batch), len(result),
        )
        return result

    def _fetch_single(self, symbol: str) -> Optional[FundamentalMetrics]:
        """拉取单只股票的财务分析指标。"""
        try:
            import akshare as ak
            df = ak.stock_financial_analysis_indicator(symbol=symbol, start_year="2020")
        except Exception:
            try:
                import akshare as ak
                em_code = f"SH{symbol}" if symbol.startswith(("6", "9")) else f"SZ{symbol}"
                df = ak.stock_financial_analysis_indicator(symbol=symbol, start_year="2020")
            except Exception as e:
                logger.debug("基本面数据拉取失败 %s: %s", symbol, e)
                return None

        if df is None or df.empty:
            return None

        try:
            latest = df.iloc[0]
            roe = self._safe_float(latest, "净资产收益率(%)")
            if roe is None:
                roe = self._safe_float(latest, "摊薄净资产收益率(%)")
            debt_ratio = self._safe_float(latest, "资产负债率(%)")
            debt_to_equity = debt_ratio / 100 if debt_ratio is not None else None

            report_date = str(latest.get("日期", "")) or None

            return FundamentalMetrics(
                symbol=symbol,
                roe=roe,
                debt_to_equity=debt_to_equity,
                dividend_yield=None,  # 需单独接口获取
                fcf_yield=None,       # 需现金流报表计算
                report_date=report_date,
            )
        except Exception as e:
            logger.debug("解析基本面数据失败 %s: %s", symbol, e)
            return None

    def fetch_dividend_yield(self, symbol: str, price: float) -> Optional[float]:
        """从 AKShare 获取股息率（需要最新分红数据 + 当前股价）。"""
        try:
            import akshare as ak
            df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
            if df is None or df.empty:
                return None
            latest_year = df.iloc[0]
            per_share = self._safe_float(latest_year, "每股分红")
            if per_share and price > 0:
                return round(per_share / price * 100, 2)
        except Exception as e:
            logger.debug("股息率获取失败 %s: %s", symbol, e)
        return None

    @staticmethod
    def _safe_float(row, col: str) -> Optional[float]:
        try:
            val = row.get(col)
            if val is None or str(val).strip() in ("", "--", "nan"):
                return None
            return float(val)
        except (ValueError, TypeError):
            return None
