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
    """轻量级 Pub/Sub，用于 Agent 簇间异步通信。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[dict], None]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, channel: str, handler: Callable[[dict], None]) -> None:
        with self._lock:
            self._subscribers.setdefault(channel, []).append(handler)

    def publish(self, channel: str, payload: dict) -> None:
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
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
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
                pick_date TEXT PRIMARY KEY,
                is_mock_data INTEGER DEFAULT 0,
                hot_sectors_json TEXT,
                candidates_count INTEGER DEFAULT 0,
                agent_calls_count INTEGER DEFAULT 0,
                aggressive_json TEXT,
                stable_json TEXT,
                total_llm_cost_usd REAL DEFAULT 0.0,
                elapsed_seconds REAL DEFAULT 0.0,
                picks_file_path TEXT,
                created_at TEXT NOT NULL
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
            CREATE INDEX IF NOT EXISTS idx_reviews_position ON position_reviews(position_id);
            """
        )
        conn.commit()

    def save_session(self, session_id: str, run_mode: str, state: dict) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, created_at, run_mode, state_json) VALUES (?, ?, ?, ?)",
            (session_id, datetime.now().isoformat(), run_mode, json.dumps(state, ensure_ascii=False, default=str)),
        )
        conn.commit()

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
        conn.commit()

    def save_trade_signal(self, session_id: str, signal: dict) -> None:
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO trade_signals
            (signal_id, session_id, symbol, action, entry_price, target_price,
             stop_loss, position_pct, strategy, rationale, timestamp, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                "active",
            ),
        )
        conn.commit()

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

    def update_signal_status(self, signal_id: str, status: str) -> None:
        conn = self._conn()
        conn.execute(
            "UPDATE trade_signals SET status = ? WHERE signal_id = ?",
            (status, signal_id),
        )
        conn.commit()

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
        conn.commit()

    def save_fill(self, fill: dict) -> None:
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO fills
            (fill_id, order_id, symbol, side, quantity, avg_price, fees, filled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fill["fill_id"],
                fill["order_id"],
                fill["symbol"],
                fill["side"],
                fill["quantity"],
                fill["avg_price"],
                fill.get("fees", 0.0),
                fill["filled_at"],
            ),
        )
        conn.commit()

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
    ) -> None:
        """Archive one day's selection result.

        ``pick_date`` is ISO date (YYYY-MM-DD) and serves as primary key;
        re-running the same day overwrites the record.
        """
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO daily_picks_archive
            (pick_date, is_mock_data, hot_sectors_json, candidates_count,
             agent_calls_count, aggressive_json, stable_json,
             total_llm_cost_usd, elapsed_seconds, picks_file_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pick_date,
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
        conn.commit()

    def get_daily_pick(self, pick_date: str) -> dict | None:
        conn = self._conn()
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
        conn.commit()

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
        conn.commit()

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
        conn.commit()

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
    ) -> None:
        """Open a new position with the original analysis thesis attached."""
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO positions
            (position_id, symbol, name, side, entry_price, current_qty, entry_date,
             status, original_signal_id, original_thesis, original_strategy,
             bull_case, bear_case, target_price, stop_loss, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                position_id, symbol, name, side, entry_price, qty, entry_date,
                signal_id, thesis, strategy, bull_case, bear_case,
                target_price, stop_loss, datetime.now().isoformat(),
            ),
        )
        conn.commit()

    def list_open_positions(self) -> list[dict]:
        conn = self._conn()
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
        conn.commit()

    def update_position_qty(self, position_id: str, new_qty: int) -> None:
        conn = self._conn()
        conn.execute(
            "UPDATE positions SET current_qty = ? WHERE position_id = ?",
            (new_qty, position_id),
        )
        conn.commit()

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
        conn.commit()

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
        conn.commit()

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
                    cls._instance._bus = EventBus()
                    cls._instance._store = MemoryStore()
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
