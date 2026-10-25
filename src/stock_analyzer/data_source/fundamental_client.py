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
    dividend_yield: Optional[float] = None   # 每股分红(元) 或 股息率 %
    fcf_yield: Optional[float] = None        # 自由/经营现金流收益率 %
    report_date: Optional[str] = None        # 最新报告期
    eps_ttm: Optional[float] = None          # TTM 每股收益 (元)
    bps: Optional[float] = None              # 每股净资产 (元)
    ocf_per_share: Optional[float] = None    # TTM 每股经营现金流 (元)
    total_shares: Optional[float] = None     # 估算总股本 (股)


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
                eps_ttm REAL,
                bps REAL,
                ocf_per_share REAL,
                total_shares REAL,
                PRIMARY KEY (symbol, fetch_date)
            )
        """)
        existing_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(fundamental_cache)").fetchall()
        }
        for col in ("eps_ttm", "bps", "ocf_per_share", "total_shares"):
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE fundamental_cache ADD COLUMN {col} REAL")
        conn.commit()
        conn.close()

    def enrich_quotes(self, quotes: list) -> None:
        """批量填充 StockQuote 的基本面字段（就地修改）。

        对每只股票：先查缓存，缓存过期或缺少估值/现金流扩展字段则从 AKShare 批量拉取后写入缓存。
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
                "SELECT roe, debt_to_equity, dividend_yield, fcf_yield, report_date, "
                "eps_ttm, bps, ocf_per_share, total_shares "
                "FROM fundamental_cache WHERE symbol=? AND fetch_date>=? "
                "ORDER BY fetch_date DESC LIMIT 1",
                (q.symbol, cutoff),
            ).fetchone()
            if row:
                m = FundamentalMetrics(
                    symbol=q.symbol,
                    roe=row[0], debt_to_equity=row[1],
                    dividend_yield=row[2], fcf_yield=row[3],
                    report_date=row[4],
                    eps_ttm=row[5], bps=row[6],
                    ocf_per_share=row[7], total_shares=row[8],
                )
                cached_map[q.symbol] = m
                # 旧缓存未包含 PE/PB/FCF 扩展字段时，纳入重拉以补齐
                if (
                    m.eps_ttm is None
                    and m.bps is None
                    and m.ocf_per_share is None
                    and m.fcf_yield is None
                ):
                    symbols_need_fetch.append(q.symbol)
            else:
                symbols_need_fetch.append(q.symbol)
        conn.close()

        # 2. 批量拉取缺失的
        if symbols_need_fetch:
            fetched = self._fetch_batch(symbols_need_fetch)
            if fetched:
                conn = sqlite3.connect(str(_CACHE_DB))
                for sym, metrics in fetched.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO fundamental_cache "
                        "(symbol, fetch_date, roe, debt_to_equity, dividend_yield, fcf_yield, "
                        "report_date, eps_ttm, bps, ocf_per_share, total_shares) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            sym, today_str, metrics.roe, metrics.debt_to_equity,
                            metrics.dividend_yield, metrics.fcf_yield, metrics.report_date,
                            metrics.eps_ttm, metrics.bps, metrics.ocf_per_share, metrics.total_shares,
                        ),
                    )
                conn.commit()
                conn.close()
                cached_map.update(fetched)

        # 3. 填充 StockQuote 字段
        for q in quotes:
            m = cached_map.get(q.symbol)
            if m:
                if m.roe is not None:
                    q.roe = m.roe
                if m.debt_to_equity is not None:
                    q.debt_to_equity = m.debt_to_equity
                # dividend_yield 存储的是每股分红金额，需结合价格计算收益率
                if m.dividend_yield is not None and q.price and q.price > 0:
                    q.dividend_yield = round(m.dividend_yield / q.price * 100, 2)
                elif m.dividend_yield is not None:
                    # 无实时价格时直接存原始值（后续有价格时可重算）
                    q.dividend_yield = m.dividend_yield

                # 现金流收益率（优先用 TTM 每股经营现金流 / 当前价 计算）
                if m.ocf_per_share is not None and q.price and q.price > 0:
                    q.fcf_yield = round(m.ocf_per_share / q.price * 100, 2)
                elif m.fcf_yield is not None:
                    q.fcf_yield = m.fcf_yield

                # 补齐 PE(TTM)、PB 与总市值（当行情快照源未提供时由财报指标+现价推算）
                if (q.pe_ttm is None or q.pe_ttm <= 0) and m.eps_ttm and m.eps_ttm > 0 and q.price and q.price > 0:
                    q.pe_ttm = round(q.price / m.eps_ttm, 2)
                if (q.pb is None or q.pb <= 0) and m.bps and m.bps > 0 and q.price and q.price > 0:
                    q.pb = round(q.price / m.bps, 2)
                if (
                    (q.market_cap_yi is None or q.market_cap_yi <= 0)
                    and m.total_shares and m.total_shares > 0
                    and q.price and q.price > 0
                ):
                    q.market_cap_yi = round(m.total_shares * q.price / 1e8, 2)

    def get_metrics(self, symbol: str) -> Optional[FundamentalMetrics]:
        """获取单只股票的基本面指标（先查缓存，再远程）。"""
        conn = sqlite3.connect(str(_CACHE_DB))
        cutoff = (date.today() - timedelta(days=_CACHE_TTL_DAYS)).isoformat()
        row = conn.execute(
            "SELECT roe, debt_to_equity, dividend_yield, fcf_yield, report_date, "
            "eps_ttm, bps, ocf_per_share, total_shares "
            "FROM fundamental_cache WHERE symbol=? AND fetch_date>=? "
            "ORDER BY fetch_date DESC LIMIT 1",
            (symbol, cutoff),
        ).fetchone()
        conn.close()

        if row and (row[5] is not None or row[6] is not None or row[7] is not None or row[3] is not None):
            return FundamentalMetrics(
                symbol=symbol,
                roe=row[0], debt_to_equity=row[1],
                dividend_yield=row[2], fcf_yield=row[3],
                report_date=row[4],
                eps_ttm=row[5], bps=row[6],
                ocf_per_share=row[7], total_shares=row[8],
            )

        fetched = self._fetch_batch([symbol])
        if symbol in fetched:
            return fetched[symbol]
        if row:
            return FundamentalMetrics(
                symbol=symbol,
                roe=row[0], debt_to_equity=row[1],
                dividend_yield=row[2], fcf_yield=row[3],
                report_date=row[4],
            )
        return None

    def _fetch_batch(self, symbols: list[str]) -> dict[str, FundamentalMetrics]:
        """从 AKShare 批量拉取基本面数据。

        使用 stock_financial_analysis_indicator_em 按个股查询。
        为避免频率限制，每只间隔 0.2s。
        价值投资池通常只有 ~150 只，全部处理。
        """
        result: dict[str, FundamentalMetrics] = {}
        batch = symbols[:200]  # 价值池通常 150 只，允许全量

        for symbol in batch:
            metrics = self._fetch_single(symbol)
            if metrics:
                result[symbol] = metrics
            time.sleep(0.2)

        logger.info(
            "FundamentalClient 批量拉取: 请求 %d 只, 成功 %d 只",
            len(batch), len(result),
        )
        return result

    def _fetch_single(self, symbol: str) -> Optional[FundamentalMetrics]:
        """拉取单只股票的财务分析指标。"""
        try:
            import akshare as ak
            df = ak.stock_financial_analysis_indicator(symbol=symbol, start_year="2023")
        except Exception as e:
            logger.debug("基本面数据拉取失败 %s: %s", symbol, e)
            return None

        if df is None or df.empty:
            return None

        try:
            # 数据按日期排列，取最近一个年报（12月）的全年 ROE
            # 如果没有年报则取最新半年报/季报
            annual_rows = df[df["日期"].astype(str).str.endswith("12-31")]
            if not annual_rows.empty:
                latest = annual_rows.iloc[-1]  # 最新年报
            else:
                latest = df.iloc[-1]  # 最新可用数据

            roe = self._safe_float(latest, "加权净资产收益率(%)")
            if roe is None:
                roe = self._safe_float(latest, "净资产收益率(%)")

            debt_ratio = self._safe_float(latest, "资产负债率(%)")
            debt_to_equity = debt_ratio / 100 if debt_ratio is not None else None

            report_date = str(latest.get("日期", "")) or None

            # 尝试获取股息率（从分红数据）
            dividend_yield = self._fetch_dividend_yield_from_df(symbol, df)

            # 提取 TTM 每股收益 (EPS)、每股净资产 (BPS)、TTM 每股经营现金流 (OCF) 与总股本
            eps_ttm = self._compute_ttm_per_share(
                df, ("摊薄每股收益(元)", "加权每股收益(元)", "每股收益_调整后(元)")
            )
            ocf_per_share = self._compute_ttm_per_share(
                df, ("每股经营性现金流(元)",)
            )
            latest_row = df.iloc[-1]
            bps = (
                self._safe_float(latest_row, "每股净资产_调整后(元)")
                or self._safe_float(latest_row, "每股净资产_调整前(元)")
                or self._safe_float(latest, "每股净资产_调整后(元)")
                or self._safe_float(latest, "每股净资产_调整前(元)")
            )
            total_shares: Optional[float] = None
            total_assets = self._safe_float(latest_row, "总资产(元)") or self._safe_float(latest, "总资产(元)")
            latest_debt = self._safe_float(latest_row, "资产负债率(%)")
            if latest_debt is None:
                latest_debt = debt_ratio
            if (
                total_assets and total_assets > 0
                and latest_debt is not None and 0 <= latest_debt < 100
                and bps and bps > 0
            ):
                equity = total_assets * (1.0 - latest_debt / 100.0)
                if equity > 0:
                    total_shares = equity / bps

            return FundamentalMetrics(
                symbol=symbol,
                roe=roe,
                debt_to_equity=debt_to_equity,
                dividend_yield=dividend_yield,
                fcf_yield=None,
                report_date=report_date,
                eps_ttm=eps_ttm,
                bps=bps,
                ocf_per_share=ocf_per_share,
                total_shares=total_shares,
            )
        except Exception as e:
            logger.debug("解析基本面数据失败 %s: %s", symbol, e)
            return None

    def _compute_ttm_per_share(
        self, df, col_candidates: tuple[str, ...]
    ) -> Optional[float]:
        """从累计口径季报/年报序列计算 TTM 每股指标（如 EPS、每股经营现金流）。"""
        by_date: dict[str, float] = {}
        for _, row in df.iterrows():
            d_str = str(row.get("日期", ""))[:10]
            if not d_str:
                continue
            val: Optional[float] = None
            for col in col_candidates:
                val = self._safe_float(row, col)
                if val is not None:
                    break
            if val is not None:
                by_date[d_str] = val

        if not by_date:
            return None

        sorted_dates = sorted(by_date.keys())
        latest_d = sorted_dates[-1]
        latest_v = by_date[latest_d]

        if latest_d.endswith("12-31"):
            return round(latest_v, 4)

        try:
            year = int(latest_d[:4])
            mmdd = latest_d[5:10]
            prev_annual_d = f"{year - 1}-12-31"
            prev_same_d = f"{year - 1}-{mmdd}"
            if prev_annual_d in by_date and prev_same_d in by_date:
                return round(by_date[prev_annual_d] + latest_v - by_date[prev_same_d], 4)
        except (ValueError, TypeError):
            pass

        annual_dates = [d for d in sorted_dates if d.endswith("12-31")]
        if annual_dates:
            return round(by_date[annual_dates[-1]], 4)

        scale_map = {"03-31": 4.0, "06-30": 2.0, "09-30": 4.0 / 3.0}
        scale = scale_map.get(latest_d[5:10], 1.0)
        return round(latest_v * scale, 4)

    def _fetch_dividend_yield_from_df(self, symbol: str, fin_df) -> Optional[float]:
        """尝试获取每股分红（返回每股派息金额，由 enrich_quotes 结合价格计算收益率）。"""
        try:
            import akshare as ak
            df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
            if df is None or df.empty:
                return None
            # 跳过"预案"状态，找已实施的分红
            impl_df = df[df["进度"].isin(["实施", "已实施"])]
            if impl_df.empty:
                # 没有已实施的，取最新一条（可能是预案）
                impl_df = df
            latest = impl_df.iloc[0]
            # 列名可能是 "派息"、"每股分红"、"派息(税前)(元)"
            # 注意：AKShare 的"派息"字段单位是"元/10股"
            per_10_share = self._safe_float(latest, "派息")
            if per_10_share is not None and per_10_share > 0:
                return per_10_share / 10.0  # 转为每股
            per_share = self._safe_float(latest, "每股分红")
            if per_share is None:
                per_share = self._safe_float(latest, "派息(税前)(元)")
            return per_share if per_share and per_share > 0 else None
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
