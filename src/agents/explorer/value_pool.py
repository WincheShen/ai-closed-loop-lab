"""价值投资选股池 — 周级别刷新的优质公司候选池。

与短期热点扫描不同，价值投资选股池:
1. 从沪深300/中证500成分股中筛选（不依赖当日活跃度）
2. 周级别刷新（基本面数据按季度变化，不需要每日重算）
3. 以 ROE/PE/分红/负债率 作为核心筛选标准
4. 缓存在 SQLite 中，同周内直接复用

用法：当 persona 为价值投资人格时，Explorer 可从此池中获取候选票。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from src.stock_analyzer.data_source.akshare_client import StockQuote

logger = logging.getLogger(__name__)

_CACHE_DIR = Path("data/cache")
_POOL_DB = _CACHE_DIR / "value_pool.db"
_POOL_TTL_DAYS = 7  # 每周刷新一次


class ValueStockPool:
    """价值投资选股池 — 从大盘成分股中筛选基本面优质标的。"""

    def __init__(self) -> None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_db()

    def _ensure_db(self) -> None:
        conn = sqlite3.connect(str(_POOL_DB))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS value_pool (
                symbol TEXT NOT NULL,
                name TEXT,
                industry TEXT,
                pe_ttm REAL,
                pb REAL,
                roe REAL,
                debt_to_equity REAL,
                dividend_yield REAL,
                market_cap_yi REAL,
                refresh_date TEXT NOT NULL,
                PRIMARY KEY (symbol, refresh_date)
            )
        """)
        conn.commit()
        conn.close()

    def get_pool(self, force_refresh: bool = False) -> list[StockQuote]:
        """获取价值投资候选池（有效期内直接返回缓存）。"""
        if not force_refresh:
            cached = self._load_from_cache()
            if cached:
                logger.info("ValueStockPool: 使用缓存 (%d 只)", len(cached))
                return cached

        # 需要刷新
        pool = self._build_pool()
        self._save_to_cache(pool)
        logger.info("ValueStockPool: 刷新完成 (%d 只)", len(pool))
        return pool

    def _load_from_cache(self) -> list[StockQuote] | None:
        """加载缓存池（7天内有效）。"""
        cutoff = (date.today() - timedelta(days=_POOL_TTL_DAYS)).isoformat()
        conn = sqlite3.connect(str(_POOL_DB))
        rows = conn.execute(
            "SELECT symbol, name, industry, pe_ttm, pb, roe, debt_to_equity, "
            "dividend_yield, market_cap_yi FROM value_pool "
            "WHERE refresh_date >= ? ORDER BY roe DESC",
            (cutoff,),
        ).fetchall()
        conn.close()

        if not rows:
            return None

        return [
            StockQuote(
                symbol=r[0], name=r[1] or "", price=0, change_pct=0,
                volume=0, turnover=0, industry=r[2] or "",
                pe_ttm=r[3], pb=r[4], roe=r[5],
                debt_to_equity=r[6], dividend_yield=r[7],
                market_cap_yi=r[8],
            )
            for r in rows
        ]

    def _save_to_cache(self, pool: list[StockQuote]) -> None:
        today_str = date.today().isoformat()
        conn = sqlite3.connect(str(_POOL_DB))
        # 清除旧数据
        conn.execute("DELETE FROM value_pool WHERE refresh_date < ?",
                     ((date.today() - timedelta(days=_POOL_TTL_DAYS * 2)).isoformat(),))
        for s in pool:
            conn.execute(
                "INSERT OR REPLACE INTO value_pool "
                "(symbol, name, industry, pe_ttm, pb, roe, debt_to_equity, "
                "dividend_yield, market_cap_yi, refresh_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (s.symbol, s.name, s.industry, s.pe_ttm, s.pb,
                 s.roe, s.debt_to_equity, s.dividend_yield,
                 s.market_cap_yi, today_str),
            )
        conn.commit()
        conn.close()

    def _build_pool(self) -> list[StockQuote]:
        """从全市场快照中筛选基本面达标的股票构建价值池。

        筛选标准（宽松版，允许后续精筛）：
        - 非 ST
        - 市值 > 100 亿
        - PE(TTM) > 0 且 < 50
        - 如果有 ROE 数据，ROE > 10%
        """
        try:
            from src.stock_analyzer.data_source import AkshareClient, FundamentalClient
            client = AkshareClient(allow_mock_fallback=True)
            snapshot = client.fetch_snapshot()

            # 初筛：市值+估值
            candidates = [
                s for s in snapshot.stocks
                if not s.is_st
                and s.market_cap_yi is not None and s.market_cap_yi >= 100
                and s.pe_ttm is not None and 0 < s.pe_ttm <= 50
            ]

            # 取市值前 200 只进行基本面数据填充
            candidates.sort(key=lambda s: s.market_cap_yi or 0, reverse=True)
            candidates = candidates[:200]

            # 填充基本面数据（200只以内，FundamentalClient 可全量处理）
            try:
                fc = FundamentalClient()
                fc.enrich_quotes(candidates)
                enriched_count = sum(1 for s in candidates if s.roe is not None)
                logger.info("ValueStockPool: 基本面填充 %d/%d 只成功", enriched_count, len(candidates))
            except Exception:
                logger.warning("ValueStockPool: 基本面数据填充失败")

            # 精筛：有 ROE 数据的优先，ROE > 10%
            pool = [
                s for s in candidates
                if s.roe is None or s.roe >= 10  # ROE 数据缺失也保留
            ]

            logger.info(
                "ValueStockPool: 全市场 %d → 初筛 %d → 精筛 %d",
                len(snapshot.stocks), len(candidates), len(pool),
            )
            return pool

        except Exception as e:
            logger.error("ValueStockPool 构建失败: %s", e)
            return []
