"""Central Brain — 元数据中心实现。

职责：
1. 统一状态持久化 (SQLite)
2. 跨 Agent 消息总线 (Pub/Sub)
3. 向量记忆存储 (sqlite-vec)
4. 事件序列化与回放
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.infra.config import cfg
from src.infra.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# EventBus — 进程内消息总线
# =============================================================================

class EventBus:
    """Pub/Sub with SQLite persistence for audit trail."""

    def __init__(self, store: MemoryStore | None = None) -> None:
        self._subscribers: dict[str, list[Callable[[dict], None]]] = {}
        self._lock = threading.Lock()
        self._store = store  # Will be set when CentralBrain initializes

    def set_store(self, store: MemoryStore) -> None:
        """Set the backing store for event persistence."""
        self._store = store

    def subscribe(self, channel: str, handler: Callable[[dict], None]) -> None:
        with self._lock:
            self._subscribers.setdefault(channel, []).append(handler)

    def publish(self, channel: str, payload: dict) -> None:
        # 1. Persist to DB first (audit trail, best-effort)
        if self._store:
            try:
                event_id = f"EVT-{uuid.uuid4().hex[:8].upper()}"
                self._store._conn().execute(
                    "INSERT INTO events (event_id, session_id, agent, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
                    (event_id, payload.get("session_id", ""), "event_bus", channel, json.dumps(payload, default=str), datetime.now().isoformat()),
                )
                self._store._conn().commit()
            except Exception as e:
                logger.warning("Event persistence failed on %s: %s", channel, e)

        # 2. In-memory dispatch
        with self._lock:
            handlers = self._subscribers.get(channel, []).copy()
        for handler in handlers:
            try:
                handler(payload)
            except Exception as e:
                logger.warning("Event handler error on %s: %s", channel, e)

    def emit_trade_signal(self, signal: dict) -> None:
        self.publish("trade_signal", signal)

    def emit_order_fill(self, fill: dict) -> None:
        self.publish("order_fill", fill)

    def emit_post_published(self, post: dict) -> None:
        self.publish("post_published", post)

    def emit_comment_received(self, comment: dict) -> None:
        self.publish("comment_received", comment)


# =============================================================================
# MemoryStore — SQLite 向量记忆
# =============================================================================

class MemoryStore:
    """基于 SQLite 的记忆存储，支持向量化检索 (sqlite-vec)。"""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or cfg().get("db_path")
        self._local = threading.local()
        self._in_transaction = False
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self.db_path, check_same_thread=False, timeout=30,
                isolation_level=None,
            )
            self._local.conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent access
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at TEXT,
                run_mode TEXT,
                state_json TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                session_id TEXT,
                agent TEXT,
                event_type TEXT,
                payload TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                session_id TEXT,
                agent TEXT,
                content TEXT,
                tags TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS trade_signals (
                signal_id TEXT PRIMARY KEY,
                session_id TEXT,
                symbol TEXT,
                action TEXT,
                entry_price REAL,
                target_price REAL,
                stop_loss REAL,
                position_pct REAL,
                strategy TEXT,
                rationale TEXT,
                timestamp TEXT,
                status TEXT DEFAULT 'active'
            );
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                session_id TEXT,
                signal_id TEXT,
                symbol TEXT,
                side TEXT,
                quantity INTEGER,
                order_type TEXT,
                limit_price REAL,
                status TEXT,
                submitted_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS fills (
                fill_id TEXT PRIMARY KEY,
                persona_id TEXT NOT NULL DEFAULT 'short_term_hot_rotation_v1',
                order_id TEXT,
                symbol TEXT,
                side TEXT,
                quantity INTEGER,
                avg_price REAL,
                fees REAL,
                filled_at TEXT
            );
            -- ---------------------------------------------------------------
            -- Phase 3.5 observability tables
            -- ---------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                request_id TEXT NOT NULL,
                symbol TEXT,
                stage TEXT,
                model TEXT NOT NULL,
                provider TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                latency_ms INTEGER,
                success INTEGER DEFAULT 1,
                error_msg TEXT,
                meta_json TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_picks_archive (
                pick_date TEXT NOT NULL,
                persona_id TEXT NOT NULL DEFAULT 'short_term_hot_rotation_v1',
                is_mock_data INTEGER DEFAULT 0,
                hot_sectors_json TEXT,
                candidates_count INTEGER DEFAULT 0,
                agent_calls_count INTEGER DEFAULT 0,
                aggressive_json TEXT,
                stable_json TEXT,
                total_llm_cost_usd REAL DEFAULT 0.0,
                elapsed_seconds REAL DEFAULT 0.0,
                picks_file_path TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (pick_date, persona_id)
            );
            CREATE TABLE IF NOT EXISTS social_posts (
                sma_task_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                platform TEXT,
                source_pick_date TEXT,
                source_symbols_json TEXT,
                topic TEXT,
                dispatched_at TEXT NOT NULL,
                sma_status TEXT DEFAULT 'pending',
                post_url TEXT,
                published_at TEXT,
                last_metrics_json TEXT,
                last_metrics_at TEXT,
                error TEXT
            );
            -- ---------------------------------------------------------------
            -- Position tracking with original thesis
            -- ---------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS positions (
                position_id TEXT PRIMARY KEY,
                persona_id TEXT NOT NULL DEFAULT 'short_term_hot_rotation_v1',
                symbol TEXT NOT NULL,
                name TEXT,
                side TEXT DEFAULT 'long',
                entry_price REAL NOT NULL,
                current_qty INTEGER DEFAULT 0,
                entry_date TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                original_signal_id TEXT,
                original_thesis TEXT,
                original_strategy TEXT,
                bull_case TEXT,
                bear_case TEXT,
                target_price REAL,
                stop_loss REAL,
                last_review_at TEXT,
                last_review_action TEXT,
                last_review_reason TEXT,
                closed_at TEXT,
                close_price REAL,
                realized_pnl REAL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS position_reviews (
                review_id TEXT PRIMARY KEY,
                position_id TEXT NOT NULL,
                review_at TEXT NOT NULL,
                current_price REAL,
                pnl_pct REAL,
                action TEXT NOT NULL,
                reason TEXT,
                market_summary TEXT,
                model TEXT,
                tokens_used INTEGER DEFAULT 0,
                FOREIGN KEY (position_id) REFERENCES positions(position_id)
            );
            -- ---------------------------------------------------------------
            -- Cognitive Agent Phase 1: MarketBrain + RiskGovernor
            -- ---------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS market_regime_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                session_id TEXT,
                trade_date TEXT NOT NULL,
                regime TEXT NOT NULL,
                risk_appetite TEXT,
                recommended_posture TEXT,
                max_total_position_pct REAL,
                hot_sectors_json TEXT,
                dominant_styles_json TEXT,
                avoid_styles_json TEXT,
                strategy_bias_json TEXT,
                daily_questions_json TEXT,
                summary TEXT,
                evidence_json TEXT,
                persona_version TEXT,
                is_mock_data INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS risk_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                signal_id TEXT NOT NULL,
                symbol TEXT,
                decision TEXT NOT NULL,
                original_position_pct REAL,
                approved_position_pct REAL,
                reason TEXT,
                risk_flags_json TEXT,
                market_regime TEXT,
                persona_version TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
            CREATE INDEX IF NOT EXISTS idx_signals_session ON trade_signals(session_id);
            CREATE INDEX IF NOT EXISTS idx_orders_session ON orders(session_id);
            CREATE INDEX IF NOT EXISTS idx_llm_ts ON llm_calls(ts);
            CREATE INDEX IF NOT EXISTS idx_llm_symbol ON llm_calls(symbol);
            CREATE INDEX IF NOT EXISTS idx_llm_request ON llm_calls(request_id);
            CREATE INDEX IF NOT EXISTS idx_posts_account ON social_posts(account_id);
            CREATE INDEX IF NOT EXISTS idx_posts_date ON social_posts(dispatched_at);
            CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
            CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
            CREATE INDEX IF NOT EXISTS idx_positions_persona ON positions(persona_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_unique_open
                ON positions(symbol, persona_id) WHERE status = 'open';
            CREATE INDEX IF NOT EXISTS idx_fills_persona ON fills(persona_id);
            CREATE INDEX IF NOT EXISTS idx_reviews_position ON position_reviews(position_id);
            CREATE INDEX IF NOT EXISTS idx_regime_date ON market_regime_snapshots(trade_date);
            CREATE INDEX IF NOT EXISTS idx_risk_session ON risk_decisions(session_id);
            CREATE INDEX IF NOT EXISTS idx_risk_signal ON risk_decisions(signal_id);
            -- ---------------------------------------------------------------
            -- Sprint 1: 交易归因 + Lesson 学习
            -- ---------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS trade_attributions (
                attribution_id TEXT PRIMARY KEY,
                position_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                entry_price REAL,
                close_price REAL,
                realized_pnl REAL,
                pnl_pct REAL,
                holding_days INTEGER,
                outcome TEXT NOT NULL,
                primary_cause TEXT NOT NULL,
                secondary_causes_json TEXT,
                entry_regime TEXT,
                close_regime TEXT,
                regime_changed INTEGER DEFAULT 0,
                strategy_id TEXT,
                original_thesis TEXT,
                actual_narrative TEXT,
                lesson TEXT,
                should_have TEXT,
                tags_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (position_id) REFERENCES positions(position_id)
            );
            CREATE TABLE IF NOT EXISTS lessons (
                lesson_id TEXT PRIMARY KEY,
                attribution_id TEXT,
                symbol TEXT,
                strategy_id TEXT,
                regime TEXT,
                outcome TEXT,
                lesson_text TEXT NOT NULL,
                tags_json TEXT,
                relevance_score REAL DEFAULT 1.0,
                cited_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_attr_position ON trade_attributions(position_id);
            CREATE INDEX IF NOT EXISTS idx_attr_outcome ON trade_attributions(outcome);
            CREATE INDEX IF NOT EXISTS idx_attr_strategy ON trade_attributions(strategy_id);
            CREATE INDEX IF NOT EXISTS idx_lessons_strategy ON lessons(strategy_id);
            CREATE INDEX IF NOT EXISTS idx_lessons_regime ON lessons(regime);
            CREATE INDEX IF NOT EXISTS idx_lessons_outcome ON lessons(outcome);
            -- ---------------------------------------------------------------
            -- Phase 2: 自选股池 (Watchlist)
            -- ---------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS watchlist (
                watch_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                name TEXT,
                sector TEXT,
                status TEXT DEFAULT 'watching',
                thesis TEXT,
                entry_condition TEXT,
                target_price REAL,
                stop_loss REAL,
                strategy_id TEXT,
                source TEXT,
                added_at TEXT NOT NULL,
                last_check_at TEXT,
                last_price REAL,
                last_change_pct REAL,
                days_watched INTEGER DEFAULT 0,
                triggered INTEGER DEFAULT 0,
                triggered_at TEXT,
                removed_at TEXT,
                remove_reason TEXT,
                notes TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_watchlist_symbol ON watchlist(symbol);
            CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist(status);
            -- ---------------------------------------------------------------
            -- Multi-Persona Account Management
            -- ---------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                persona_id TEXT NOT NULL UNIQUE,
                persona_name TEXT,
                capital REAL NOT NULL,
                available_cash REAL NOT NULL,
                total_value REAL NOT NULL,
                total_pnl REAL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_accounts_persona ON accounts(persona_id);
            """
        )
        conn.commit()
        # 兼容旧数据库：为已存在的表补字段
        self._apply_phase1_migrations(conn)
        self._apply_multi_persona_migrations(conn)

    def _apply_phase1_migrations(self, conn: sqlite3.Connection) -> None:
        """为 Phase 1 增加 trade_signals/positions 的认知元数据字段。

        使用 ALTER TABLE ADD COLUMN，每个字段单独 try/except 防止重复执行报错。
        """
        migrations = [
            ("trade_signals", "market_regime TEXT"),
            ("trade_signals", "persona_version TEXT"),
            ("trade_signals", "risk_decision TEXT"),
            ("trade_signals", "approved_position_pct REAL"),
            ("trade_signals", "entry_condition TEXT DEFAULT 'immediate'"),
            ("trade_signals", "current_price REAL"),
            ("positions", "market_regime TEXT"),
            ("positions", "persona_version TEXT"),
            ("positions", "sector TEXT"),
        ]
        for table, col_def in migrations:
            col_name = col_def.split()[0]
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    logger.debug("ALTER %s.%s skipped: %s", table, col_name, e)
        conn.commit()

    def _apply_multi_persona_migrations(self, conn: sqlite3.Connection) -> None:
        """为多人格支持添加 account_id 和 persona_id 字段。"""
        migrations = [
            ("positions", "account_id TEXT"),
            ("trade_signals", "account_id TEXT"),
            ("positions", "persona_id TEXT NOT NULL DEFAULT 'short_term_hot_rotation_v1'"),
            ("fills", "persona_id TEXT NOT NULL DEFAULT 'short_term_hot_rotation_v1'"),
            # P1: 选股和风控也需要人格标识
            ("trade_signals", "persona_id TEXT"),
            ("risk_decisions", "persona_id TEXT"),
            # P1: 选股归档表支持多人格
            ("daily_picks_archive", "persona_id TEXT NOT NULL DEFAULT 'short_term_hot_rotation_v1'"),
        ]
        for table, col_def in migrations:
            col_name = col_def.split()[0]
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    logger.debug("ALTER %s.%s skipped: %s", table, col_name, e)
        conn.commit()

    # ------------------------------------------------------------------
    # Transaction helpers
    # ------------------------------------------------------------------

    def begin_transaction(self) -> None:
        """Begin an IMMEDIATE transaction (acquires write lock)."""
        self._conn().execute("BEGIN IMMEDIATE")
        self._in_transaction = True

    def commit_transaction(self) -> None:
        """Commit the current transaction."""
        self._in_transaction = False
        self._conn().commit()

    def rollback_transaction(self) -> None:
        """Rollback the current transaction."""
        self._in_transaction = False
        self._conn().rollback()

    def _auto_commit(self) -> None:
        """Commit only when not inside a managed transaction."""
        if not self._in_transaction:
            self._conn().commit()

    # ------------------------------------------------------------------
    # Position queries (generic)
    # ------------------------------------------------------------------

    def list_positions(
        self,
        persona_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """List positions with optional persona_id and status filters."""
        conn = self._conn()
        conditions: list[str] = []
        params: list[Any] = []
        if persona_id:
            conditions.append("persona_id = ?")
            params.append(persona_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM positions {where} ORDER BY entry_date",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def save_session(self, session_id: str, run_mode: str, state: dict) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, created_at, run_mode, state_json) VALUES (?, ?, ?, ?)",
            (session_id, datetime.now().isoformat(), run_mode, json.dumps(state, ensure_ascii=False, default=str)),
        )
        self._auto_commit()

    def load_session(self, session_id: str) -> dict | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT state_json FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row:
            return json.loads(row["state_json"])
        return None

    def log_event(self, session_id: str, agent: str, event_type: str, payload: dict) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT INTO events (event_id, session_id, agent, event_type, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                session_id,
                agent,
                event_type,
                json.dumps(payload, ensure_ascii=False, default=str),
                datetime.now().isoformat(),
            ),
        )
        self._auto_commit()

    def save_trade_signal(self, session_id: str, signal: dict) -> None:
        conn = self._conn()
        entry_condition = signal.get("entry_condition", "immediate")
        status = "pending" if entry_condition in ("breakout", "pullback") else "active"
        conn.execute(
            """INSERT OR REPLACE INTO trade_signals
            (signal_id, session_id, symbol, action, entry_price, target_price,
             stop_loss, position_pct, strategy, rationale, timestamp, status,
             market_regime, persona_version, risk_decision, approved_position_pct,
             entry_condition, current_price, persona_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                signal["signal_id"],
                session_id,
                signal["symbol"],
                signal["action"],
                signal.get("entry_price"),
                signal.get("target_price"),
                signal.get("stop_loss"),
                signal.get("position_pct"),
                signal.get("strategy"),
                signal.get("rationale"),
                signal.get("timestamp"),
                status,
                signal.get("market_regime"),
                signal.get("persona_version"),
                signal.get("risk_decision"),
                signal.get("approved_position_pct"),
                entry_condition,
                signal.get("current_price"),
                signal.get("persona_id"),  # P1: 人格标识
            ),
        )
        self._auto_commit()

    # ------------------------------------------------------------------
    # Phase 1: MarketBrain + RiskGovernor 持久化
    # ------------------------------------------------------------------

    def save_market_regime(self, session_id: str, snapshot: dict) -> None:
        """保存 MarketBrain 输出的 regime 快照。"""
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO market_regime_snapshots
            (snapshot_id, session_id, trade_date, regime, risk_appetite,
             recommended_posture, max_total_position_pct,
             hot_sectors_json, dominant_styles_json, avoid_styles_json,
             strategy_bias_json, daily_questions_json,
             summary, evidence_json, persona_version, is_mock_data, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot["snapshot_id"],
                session_id,
                snapshot["trade_date"],
                snapshot["regime"],
                snapshot.get("risk_appetite"),
                snapshot.get("recommended_posture"),
                snapshot.get("max_total_position_pct"),
                json.dumps(snapshot.get("hot_sectors", []), ensure_ascii=False),
                json.dumps(snapshot.get("dominant_styles", []), ensure_ascii=False),
                json.dumps(snapshot.get("avoid_styles", []), ensure_ascii=False),
                json.dumps(snapshot.get("strategy_bias", {}), ensure_ascii=False),
                json.dumps(snapshot.get("daily_questions", []), ensure_ascii=False),
                snapshot.get("summary"),
                json.dumps(snapshot.get("evidence", {}), ensure_ascii=False, default=str),
                snapshot.get("persona_version"),
                1 if snapshot.get("is_mock_data") else 0,
                snapshot.get("created_at") or datetime.now().isoformat(),
            ),
        )
        self._auto_commit()

    def latest_market_regime(self) -> dict | None:
        """获取最近一次的 market regime 快照。"""
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM market_regime_snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def save_risk_decision(self, session_id: str, decision: dict) -> None:
        """保存单条 RiskGovernor 裁决。"""
        conn = self._conn()
        conn.execute(
            """INSERT INTO risk_decisions
            (session_id, signal_id, symbol, decision,
             original_position_pct, approved_position_pct, reason,
             risk_flags_json, market_regime, persona_version, created_at, persona_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                decision["signal_id"],
                decision.get("symbol"),
                decision["decision"],
                decision.get("original_position_pct"),
                decision.get("approved_position_pct"),
                decision.get("reason"),
                json.dumps(decision.get("risk_flags", []), ensure_ascii=False),
                decision.get("market_regime"),
                decision.get("persona_version"),
                decision.get("created_at") or datetime.now().isoformat(),
                decision.get("persona_id"),  # P1: 人格标识
            ),
        )
        self._auto_commit()

    def list_active_signals(self, session_id: str | None = None) -> list[dict]:
        conn = self._conn()
        if session_id:
            rows = conn.execute(
                "SELECT * FROM trade_signals WHERE session_id = ? AND status = 'active'",
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trade_signals WHERE status = 'active'"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_pending_signals(self) -> list[dict]:
        """获取所有待触发的条件单 (status=pending, 未过期)。"""
        conn = self._conn()
        rows = conn.execute(
            """SELECT * FROM trade_signals
            WHERE status = 'pending'
              AND (timestamp IS NULL OR datetime(timestamp, '+5 days') >= datetime('now'))
            ORDER BY timestamp""",
        ).fetchall()
        return [dict(r) for r in rows]

    def update_signal_status(self, signal_id: str, status: str) -> None:
        conn = self._conn()
        conn.execute(
            "UPDATE trade_signals SET status = ? WHERE signal_id = ?",
            (status, signal_id),
        )
        self._auto_commit()

    def save_order(self, order: dict) -> None:
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO orders
            (order_id, session_id, signal_id, symbol, side, quantity, order_type,
             limit_price, status, submitted_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order["order_id"],
                order.get("session_id"),
                order.get("signal_id"),
                order["symbol"],
                order["side"],
                order.get("quantity", 0),
                order.get("order_type", "market"),
                order.get("limit_price"),
                order["status"],
                order.get("submitted_at"),
                order.get("updated_at"),
            ),
        )
        self._auto_commit()

    def save_fill(self, fill: dict) -> None:
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO fills
            (fill_id, persona_id, order_id, symbol, side, quantity, avg_price, fees, filled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fill["fill_id"],
                fill.get("persona_id", "short_term_hot_rotation_v1"),
                fill["order_id"],
                fill["symbol"],
                fill["side"],
                fill["quantity"],
                fill["avg_price"],
                fill.get("fees", 0.0),
                fill["filled_at"],
            ),
        )
        self._auto_commit()

    def query_events(self, session_id: str | None = None, agent: str | None = None, limit: int = 100) -> list[dict]:
        conn = self._conn()
        sql = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        if agent:
            sql += " AND agent = ?"
            params.append(agent)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Phase 3.5 observability methods
    # ------------------------------------------------------------------

    def save_daily_pick(
        self,
        pick_date: str,
        is_mock_data: bool,
        hot_sectors: list[str],
        aggressive: list[dict],
        stable: list[dict],
        candidates_count: int = 0,
        agent_calls_count: int = 0,
        total_llm_cost_usd: float = 0.0,
        elapsed_seconds: float = 0.0,
        picks_file_path: str | None = None,
        persona_id: str | None = None,
    ) -> None:
        """Archive one day's selection result.

        ``pick_date`` is ISO date (YYYY-MM-DD);
        ``persona_id`` identifies the trading persona;
        re-running the same day + persona overwrites the record.
        """
        conn = self._conn()
        persona = persona_id or "short_term_hot_rotation_v1"
        conn.execute(
            """INSERT OR REPLACE INTO daily_picks_archive
            (pick_date, persona_id, is_mock_data, hot_sectors_json, candidates_count,
             agent_calls_count, aggressive_json, stable_json,
             total_llm_cost_usd, elapsed_seconds, picks_file_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pick_date,
                persona,
                1 if is_mock_data else 0,
                json.dumps(hot_sectors, ensure_ascii=False),
                candidates_count,
                agent_calls_count,
                json.dumps(aggressive, ensure_ascii=False, default=str),
                json.dumps(stable, ensure_ascii=False, default=str),
                total_llm_cost_usd,
                elapsed_seconds,
                picks_file_path,
                datetime.now().isoformat(),
            ),
        )
        self._auto_commit()

    def get_daily_pick(self, pick_date: str, persona_id: str | None = None) -> dict | None:
        conn = self._conn()
        if persona_id:
            row = conn.execute(
                "SELECT * FROM daily_picks_archive WHERE pick_date = ? AND persona_id = ?",
                (pick_date, persona_id),
            ).fetchone()
        else:
            # 默认返回短线热点的数据（向后兼容）
            row = conn.execute(
                "SELECT * FROM daily_picks_archive WHERE pick_date = ? AND persona_id = ?",
                (pick_date, "short_term_hot_rotation_v1"),
            ).fetchone()
            # 如果没有找到，尝试返回任意一条（旧数据兼容）
            if not row:
                row = conn.execute(
                    "SELECT * FROM daily_picks_archive WHERE pick_date = ?",
                    (pick_date,),
                ).fetchone()
        return dict(row) if row else None

    def record_social_post(
        self,
        sma_task_id: str,
        account_id: str,
        platform: str | None = None,
        source_pick_date: str | None = None,
        source_symbols: list[str] | None = None,
        topic: str | None = None,
        dispatched_at: str | None = None,
    ) -> None:
        """Register a dispatched social-media task so we can follow-up later."""
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO social_posts
            (sma_task_id, account_id, platform, source_pick_date,
             source_symbols_json, topic, dispatched_at, sma_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                sma_task_id,
                account_id,
                platform,
                source_pick_date,
                json.dumps(source_symbols or [], ensure_ascii=False),
                topic,
                dispatched_at or datetime.now().isoformat(),
            ),
        )
        self._auto_commit()

    def update_social_post_metrics(
        self,
        sma_task_id: str,
        sma_status: str | None = None,
        post_url: str | None = None,
        published_at: str | None = None,
        last_metrics: dict | None = None,
        error: str | None = None,
    ) -> None:
        """Patch the tracking row, e.g. when sync_sma_engagements pulls updates."""
        conn = self._conn()
        fields: list[str] = []
        params: list[Any] = []
        if sma_status is not None:
            fields.append("sma_status = ?")
            params.append(sma_status)
        if post_url is not None:
            fields.append("post_url = ?")
            params.append(post_url)
        if published_at is not None:
            fields.append("published_at = ?")
            params.append(published_at)
        if last_metrics is not None:
            fields.append("last_metrics_json = ?")
            params.append(json.dumps(last_metrics, ensure_ascii=False))
            fields.append("last_metrics_at = ?")
            params.append(datetime.now().isoformat())
        if error is not None:
            fields.append("error = ?")
            params.append(error)
        if not fields:
            return
        params.append(sma_task_id)
        conn.execute(
            f"UPDATE social_posts SET {', '.join(fields)} WHERE sma_task_id = ?",
            params,
        )
        self._auto_commit()

    def list_social_posts(
        self,
        account_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        conn = self._conn()
        if account_id:
            rows = conn.execute(
                "SELECT * FROM social_posts WHERE account_id = ? "
                "ORDER BY dispatched_at DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM social_posts ORDER BY dispatched_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_llm_call(
        self,
        request_id: str,
        model: str,
        symbol: str | None = None,
        stage: str | None = None,
        provider: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int | None = None,
        success: bool = True,
        error_msg: str | None = None,
        meta: dict | None = None,
    ) -> None:
        """Record a single LLM call for cost/latency observability."""
        conn = self._conn()
        conn.execute(
            """INSERT INTO llm_calls
            (ts, request_id, symbol, stage, model, provider,
             prompt_tokens, completion_tokens, total_tokens, cost_usd,
             latency_ms, success, error_msg, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                request_id,
                symbol,
                stage,
                model,
                provider,
                prompt_tokens,
                completion_tokens,
                total_tokens or (prompt_tokens + completion_tokens),
                cost_usd,
                latency_ms,
                1 if success else 0,
                error_msg,
                json.dumps(meta, ensure_ascii=False) if meta else None,
            ),
        )
        self._auto_commit()

    def llm_cost_summary(
        self,
        since: str | None = None,
        until: str | None = None,
    ) -> dict:
        """Aggregate LLM spend over an optional ISO-timestamp window."""
        conn = self._conn()
        sql = "SELECT COUNT(*) AS n, COALESCE(SUM(total_tokens), 0) AS tokens, " \
              "COALESCE(SUM(cost_usd), 0.0) AS cost_usd FROM llm_calls WHERE 1=1"
        params: list[Any] = []
        if since:
            sql += " AND ts >= ?"
            params.append(since)
        if until:
            sql += " AND ts <= ?"
            params.append(until)
        row = conn.execute(sql, params).fetchone()
        return {
            "total_calls": row["n"],
            "total_tokens": row["tokens"],
            "total_cost_usd": round(row["cost_usd"], 4),
        }

    # ------------------------------------------------------------------
    # Position & thesis management
    # ------------------------------------------------------------------

    def open_position(
        self,
        position_id: str,
        symbol: str,
        entry_price: float,
        qty: int,
        entry_date: str,
        name: str = "",
        side: str = "long",
        signal_id: str | None = None,
        thesis: str | None = None,
        strategy: str | None = None,
        bull_case: str | None = None,
        bear_case: str | None = None,
        target_price: float | None = None,
        stop_loss: float | None = None,
        market_regime: str | None = None,
        persona_version: str | None = None,
        sector: str | None = None,
        persona_id: str | None = None,
    ) -> None:
        """Open a new position with the original analysis thesis attached."""
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO positions
            (position_id, persona_id, symbol, name, side, entry_price, current_qty, entry_date,
             status, original_signal_id, original_thesis, original_strategy,
             bull_case, bear_case, target_price, stop_loss, created_at,
             market_regime, persona_version, sector)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                position_id, persona_id or "short_term_hot_rotation_v1", symbol, name, side,
                entry_price, qty, entry_date,
                signal_id, thesis, strategy, bull_case, bear_case,
                target_price, stop_loss, datetime.now().isoformat(),
                market_regime, persona_version, sector,
            ),
        )
        self._auto_commit()

    def list_open_positions(
        self,
        persona_id: str | None = None,
        include_unassigned: bool = False,
    ) -> list[dict]:
        """列出所有 open 状态的持仓。

        Args:
            persona_id: 按人格过滤。为 None 时返回全部。
            include_unassigned: 当 persona_id 非空时，是否同时返回 persona_id IS NULL 的
                历史遗留持仓。默认 False 以避免多人格重复 review 同一持仓。
                如需回收未指派持仓，应通过数据迁移把 NULL 更新为默认人格，
                而不是让多个人格并行 review。
        """
        conn = self._conn()
        if persona_id:
            if include_unassigned:
                rows = conn.execute(
                    "SELECT * FROM positions WHERE status = 'open' "
                    "AND (persona_id = ? OR persona_id IS NULL) "
                    "ORDER BY entry_date",
                    (persona_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM positions WHERE status = 'open' AND persona_id = ? "
                    "ORDER BY entry_date",
                    (persona_id,),
                ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM positions WHERE status = 'open' ORDER BY entry_date"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_position(self, position_id: str) -> dict | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM positions WHERE position_id = ?", (position_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_position_review(
        self,
        position_id: str,
        action: str,
        reason: str,
        review_at: str | None = None,
    ) -> None:
        """Update position with latest review result."""
        conn = self._conn()
        ts = review_at or datetime.now().isoformat()
        conn.execute(
            """UPDATE positions
            SET last_review_at = ?, last_review_action = ?, last_review_reason = ?
            WHERE position_id = ?""",
            (ts, action, reason, position_id),
        )
        self._auto_commit()

    def update_position_qty(self, position_id: str, new_qty: int) -> None:
        conn = self._conn()
        conn.execute(
            "UPDATE positions SET current_qty = ? WHERE position_id = ?",
            (new_qty, position_id),
        )
        self._auto_commit()

    def close_position(
        self,
        position_id: str,
        close_price: float,
        realized_pnl: float,
        closed_at: str | None = None,
    ) -> None:
        conn = self._conn()
        ts = closed_at or datetime.now().isoformat()
        conn.execute(
            """UPDATE positions
            SET status = 'closed', close_price = ?, realized_pnl = ?,
                closed_at = ?, current_qty = 0
            WHERE position_id = ?""",
            (close_price, realized_pnl, ts, position_id),
        )
        self._auto_commit()

    def save_position_review(
        self,
        review_id: str,
        position_id: str,
        current_price: float,
        pnl_pct: float,
        action: str,
        reason: str,
        market_summary: str = "",
        model: str = "",
        tokens_used: int = 0,
    ) -> None:
        """Persist one review record for audit trail."""
        conn = self._conn()
        conn.execute(
            """INSERT INTO position_reviews
            (review_id, position_id, review_at, current_price, pnl_pct,
             action, reason, market_summary, model, tokens_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                review_id, position_id, datetime.now().isoformat(),
                current_price, pnl_pct, action, reason,
                market_summary, model, tokens_used,
            ),
        )
        self._auto_commit()

    def list_position_reviews(
        self, position_id: str, limit: int = 50,
    ) -> list[dict]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM position_reviews WHERE position_id = ? "
            "ORDER BY review_at DESC LIMIT ?",
            (position_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Sprint 1: 交易归因 + Lesson 持久化
    # ------------------------------------------------------------------

    def save_attribution(self, attr: dict) -> None:
        """保存交易归因记录。"""
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO trade_attributions
            (attribution_id, position_id, symbol, name,
             entry_price, close_price, realized_pnl, pnl_pct, holding_days,
             outcome, primary_cause, secondary_causes_json,
             entry_regime, close_regime, regime_changed,
             strategy_id, original_thesis, actual_narrative,
             lesson, should_have, tags_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                attr["attribution_id"],
                attr["position_id"],
                attr["symbol"],
                attr.get("name", ""),
                attr.get("entry_price"),
                attr.get("close_price"),
                attr.get("realized_pnl"),
                attr.get("pnl_pct"),
                attr.get("holding_days"),
                attr["outcome"],
                attr["primary_cause"],
                json.dumps(attr.get("secondary_causes", []), ensure_ascii=False),
                attr.get("entry_regime", ""),
                attr.get("close_regime", ""),
                1 if attr.get("regime_changed") else 0,
                attr.get("strategy_id", ""),
                attr.get("original_thesis", ""),
                attr.get("actual_narrative", ""),
                attr.get("lesson", ""),
                attr.get("should_have", ""),
                json.dumps(attr.get("tags", []), ensure_ascii=False),
                attr.get("created_at") or datetime.now().isoformat(),
            ),
        )
        self._auto_commit()

    def list_recent_attributions(self, limit: int = 10) -> list[dict]:
        """获取最近的交易归因记录。"""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM trade_attributions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def save_lesson(self, lesson: dict) -> None:
        """保存单条 lesson。"""
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO lessons
            (lesson_id, attribution_id, symbol, strategy_id, regime,
             outcome, lesson_text, tags_json, relevance_score, cited_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lesson["lesson_id"],
                lesson.get("attribution_id", ""),
                lesson.get("symbol", ""),
                lesson.get("strategy_id", ""),
                lesson.get("regime", ""),
                lesson.get("outcome", ""),
                lesson["lesson_text"],
                json.dumps(lesson.get("tags", []), ensure_ascii=False),
                lesson.get("relevance_score", 1.0),
                lesson.get("cited_count", 0),
                lesson.get("created_at") or datetime.now().isoformat(),
            ),
        )
        self._auto_commit()

    def get_recent_lessons(
        self,
        strategy_id: str | None = None,
        regime: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """检索最近 lesson，支持按 strategy_id / regime 过滤。

        排序逻辑: 同 strategy + 同 regime 的优先 (relevance_score DESC, 时间 DESC)。
        """
        conn = self._conn()
        conditions = []
        params: list[Any] = []

        if strategy_id:
            conditions.append("strategy_id = ?")
            params.append(strategy_id)
        if regime:
            conditions.append("regime = ?")
            params.append(regime)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        rows = conn.execute(
            f"SELECT * FROM lessons {where} "
            f"ORDER BY relevance_score DESC, created_at DESC LIMIT ?",
            params,
        ).fetchall()

        # 如果带过滤条件查不到足够结果，补充无过滤的最新 lesson
        results = [dict(r) for r in rows]
        if len(results) < limit and conditions:
            seen = {r["lesson_id"] for r in results}
            fill_rows = conn.execute(
                "SELECT * FROM lessons ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            for r in fill_rows:
                if dict(r)["lesson_id"] not in seen:
                    results.append(dict(r))
                    if len(results) >= limit:
                        break

        return results[:limit]

    def get_attributions_since(self, days: int = 7) -> list[dict]:
        """获取最近 N 天的归因记录。"""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM trade_attributions WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_attribution_by_position(self, position_id: str) -> dict | None:
        """获取某仓位的归因记录。"""
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM trade_attributions WHERE position_id = ?",
            (position_id,),
        ).fetchone()
        return dict(row) if row else None

    def increment_lesson_cited(self, lesson_id: str) -> None:
        """lesson 被 Strategist 引用时 +1。"""
        conn = self._conn()
        conn.execute(
            "UPDATE lessons SET cited_count = cited_count + 1 WHERE lesson_id = ?",
            (lesson_id,),
        )
        self._auto_commit()

    # ------------------------------------------------------------------
    # Watchlist (自选股池)
    # ------------------------------------------------------------------

    def add_to_watchlist(self, item: dict) -> None:
        """新增自选股到 watchlist。"""
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO watchlist
            (watch_id, symbol, name, sector, status, thesis,
             entry_condition, target_price, stop_loss, strategy_id,
             source, added_at, last_check_at, last_price, last_change_pct,
             days_watched, triggered, triggered_at, removed_at, remove_reason, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item["watch_id"],
                item["symbol"],
                item.get("name", ""),
                item.get("sector", ""),
                item.get("status", "watching"),
                item.get("thesis", ""),
                item.get("entry_condition", ""),
                item.get("target_price"),
                item.get("stop_loss"),
                item.get("strategy_id", ""),
                item.get("source", ""),
                item.get("added_at") or datetime.now().isoformat(),
                item.get("last_check_at"),
                item.get("last_price"),
                item.get("last_change_pct"),
                item.get("days_watched", 0),
                1 if item.get("triggered") else 0,
                item.get("triggered_at"),
                item.get("removed_at"),
                item.get("remove_reason"),
                item.get("notes", ""),
            ),
        )
        self._auto_commit()

    def get_watchlist(self, status: str = "watching") -> list[dict]:
        """获取指定状态的自选股列表。"""
        conn = self._conn()
        if status == "all":
            rows = conn.execute(
                "SELECT * FROM watchlist ORDER BY added_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM watchlist WHERE status = ? ORDER BY added_at DESC",
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_watchlist_symbols(self) -> list[str]:
        """仅获取当前 watching 状态的 symbol 列表。"""
        conn = self._conn()
        rows = conn.execute(
            "SELECT symbol FROM watchlist WHERE status = 'watching'"
        ).fetchall()
        return [r["symbol"] for r in rows]

    def update_watchlist_check(self, watch_id: str, price: float, change_pct: float) -> None:
        """更新每日检查结果。"""
        conn = self._conn()
        conn.execute(
            """UPDATE watchlist SET
               last_check_at = ?, last_price = ?, last_change_pct = ?,
               days_watched = days_watched + 1
            WHERE watch_id = ?""",
            (datetime.now().isoformat(), price, change_pct, watch_id),
        )
        self._auto_commit()

    def trigger_watchlist_item(self, watch_id: str) -> None:
        """标记自选股触发入场条件。"""
        conn = self._conn()
        conn.execute(
            "UPDATE watchlist SET triggered = 1, triggered_at = ?, status = 'triggered' "
            "WHERE watch_id = ?",
            (datetime.now().isoformat(), watch_id),
        )
        self._auto_commit()

    def remove_from_watchlist(self, watch_id: str, reason: str = "") -> None:
        """从自选股池移除（标记为 removed）。"""
        conn = self._conn()
        conn.execute(
            "UPDATE watchlist SET status = 'removed', removed_at = ?, remove_reason = ? "
            "WHERE watch_id = ?",
            (datetime.now().isoformat(), reason, watch_id),
        )
        self._auto_commit()

    # ------------------------------------------------------------------
    # Multi-Persona Account Management
    # ------------------------------------------------------------------

    def create_account(self, persona_id: str, persona_name: str, capital: float) -> str:
        """为指定人格创建资金账户。"""
        conn = self._conn()
        account_id = f"acc-{persona_id}"
        now = datetime.now().isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO accounts
            (account_id, persona_id, persona_name, capital, available_cash, total_value, total_pnl, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, persona_id, persona_name, capital, capital, capital, 0.0, now, now),
        )
        self._auto_commit()
        return account_id

    def get_account_by_persona(self, persona_id: str) -> dict | None:
        """根据人格 ID 获取账户信息。"""
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM accounts WHERE persona_id = ?", (persona_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_account_balance(
        self,
        account_id: str,
        available_cash: float | None = None,
        total_value: float | None = None,
        total_pnl: float | None = None,
    ) -> None:
        """更新账户余额。"""
        conn = self._conn()
        updates = []
        params = []
        if available_cash is not None:
            updates.append("available_cash = ?")
            params.append(available_cash)
        if total_value is not None:
            updates.append("total_value = ?")
            params.append(total_value)
        if total_pnl is not None:
            updates.append("total_pnl = ?")
            params.append(total_pnl)
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(account_id)

        conn.execute(
            f"UPDATE accounts SET {', '.join(updates)} WHERE account_id = ?",
            params,
        )
        self._auto_commit()

    def list_accounts(self) -> list[dict]:
        """列出所有账户。"""
        conn = self._conn()
        rows = conn.execute("SELECT * FROM accounts ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]


# 别名以保持向后兼容
MetadataStore = MemoryStore


# =============================================================================
# CentralBrain — 单例门面
# =============================================================================

class CentralBrain:
    """元数据中心门面，聚合 EventBus + MemoryStore。"""

    _instance: CentralBrain | None = None
    _lock = threading.Lock()

    def __new__(cls) -> CentralBrain:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._store = MemoryStore()
                    cls._instance._bus = EventBus()
                    cls._instance._bus.set_store(cls._instance._store)
        return cls._instance

    @property
    def bus(self) -> EventBus:
        return self._bus

    @property
    def store(self) -> MemoryStore:
        return self._store

    def persist_state(self, session_id: str, run_mode: str, state: dict) -> None:
        self._store.save_session(session_id, run_mode, state)

    def load_state(self, session_id: str) -> dict | None:
        return self._store.load_session(session_id)

    def log_agent_event(self, session_id: str, agent: str, event_type: str, payload: dict) -> None:
        self._store.log_event(session_id, agent, event_type, payload)
        # 同时通过总线广播
        self._bus.publish(event_type, {"session_id": session_id, "agent": agent, "payload": payload})


def get_central_brain() -> CentralBrain:
    return CentralBrain()
