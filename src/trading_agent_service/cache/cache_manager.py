"""TradingAgent Cache Manager.

设计要点（对应需求 FR-3.2）：
- 缓存键：(symbol, date) 维度
- 失效条件：
    1. 当前价超出报告中的 reevaluation_price_range
    2. 报告超过 N 天（默认 3 天，由 valid_until 控制）
    3. force_refresh=True
- 持久化：SQLite 元数据 + JSON 文件全文
- 线程安全：使用单连接 + check_same_thread=False，FastAPI 单进程内安全
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from ..api.schemas import Report


_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    symbol            TEXT NOT NULL,
    evaluated_date    TEXT NOT NULL,             -- YYYY-MM-DD
    evaluated_at      TEXT NOT NULL,             -- ISO datetime
    valid_until       TEXT NOT NULL,             -- YYYY-MM-DD
    price_low         REAL NOT NULL,
    price_high        REAL NOT NULL,
    final_decision    TEXT NOT NULL,
    report_path       TEXT NOT NULL,             -- 相对 root 的 JSON 文件路径
    knowledge_planet_url TEXT,
    PRIMARY KEY (symbol, evaluated_date)
);

CREATE INDEX IF NOT EXISTS idx_reports_symbol ON reports(symbol);
CREATE INDEX IF NOT EXISTS idx_reports_valid  ON reports(valid_until);
"""


@dataclass
class CacheLookupResult:
    hit: bool
    report: Optional[Report] = None
    knowledge_planet_url: Optional[str] = None
    reason: str = ""  # 命中/未命中的原因，便于调试


class CacheManager:
    """SQLite + 文件存储的报告缓存。"""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.reports_dir = self.root / "reports"
        self.reports_dir.mkdir(exist_ok=True)
        self.db_path = self.root / "cache.sqlite"

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self.db_path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def lookup(self, symbol: str, current_price: Optional[float] = None) -> CacheLookupResult:
        """查找最新有效缓存。

        Args:
            symbol: 股票代码
            current_price: 若提供，则会做价格区间失效检查
        """
        today = date.today().isoformat()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM reports
                WHERE symbol = ? AND valid_until >= ?
                ORDER BY evaluated_date DESC
                LIMIT 1
                """,
                (symbol, today),
            ).fetchone()

        if row is None:
            return CacheLookupResult(hit=False, reason="no valid cache entry")

        # 价格区间检查
        if current_price is not None:
            if current_price < row["price_low"] or current_price > row["price_high"]:
                return CacheLookupResult(
                    hit=False,
                    reason=(
                        f"current price {current_price:.2f} outside "
                        f"[{row['price_low']:.2f}, {row['price_high']:.2f}]"
                    ),
                )

        # 加载报告全文
        report_path = self.root / row["report_path"]
        if not report_path.exists():
            return CacheLookupResult(hit=False, reason=f"report file missing: {report_path}")

        with report_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        try:
            report = Report.model_validate(data)
        except Exception as e:  # noqa: BLE001
            return CacheLookupResult(hit=False, reason=f"report parse failed: {e}")

        return CacheLookupResult(
            hit=True,
            report=report,
            knowledge_planet_url=row["knowledge_planet_url"],
            reason=f"cache hit (evaluated={row['evaluated_date']})",
        )

    # ------------------------------------------------------------------
    # Store
    # ------------------------------------------------------------------

    def store(
        self,
        report: Report,
        evaluated_at: datetime,
        knowledge_planet_url: Optional[str] = None,
    ) -> None:
        """落库 + 落盘。同 (symbol, date) 会覆盖。"""
        evaluated_date = evaluated_at.date().isoformat()
        rel_path = f"reports/{report.symbol}_{evaluated_date}.json"
        abs_path = self.root / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)

        with abs_path.open("w", encoding="utf-8") as f:
            json.dump(report.model_dump(mode="json"), f, ensure_ascii=False, indent=2)

        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO reports (
                    symbol, evaluated_date, evaluated_at, valid_until,
                    price_low, price_high, final_decision,
                    report_path, knowledge_planet_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.symbol,
                    evaluated_date,
                    evaluated_at.isoformat(),
                    report.valid_until.isoformat(),
                    report.reevaluation_price_range[0],
                    report.reevaluation_price_range[1],
                    report.final_decision,
                    rel_path,
                    knowledge_planet_url,
                ),
            )

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def total_cached(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM reports").fetchone()
        return int(row["c"])

    def update_knowledge_planet_url(
        self, symbol: str, evaluated_date: str, url: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE reports SET knowledge_planet_url = ? WHERE symbol = ? AND evaluated_date = ?",
                (url, symbol, evaluated_date),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
