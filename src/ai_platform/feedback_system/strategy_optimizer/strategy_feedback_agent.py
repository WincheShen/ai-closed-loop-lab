from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Any


DB_DIR = Path("data/strategy_feedback")
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "strategy_metrics.sqlite"


class StrategyFeedbackAgent:
    """
    Agent responsible for collecting simple strategy performance signals
    from system events.

    This is the first step toward a full feedback loop.
    """

    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                payload_json TEXT,
                created_at TEXT
            )
            """
        )

        self.conn.commit()

    def _record_event(self, event_type: str, payload: Dict[str, Any]):
        self.conn.execute(
            "INSERT INTO strategy_events (event_type, payload_json, created_at) VALUES (?, ?, ?)",
            (
                event_type,
                json.dumps(payload, ensure_ascii=False),
                datetime.utcnow().isoformat(),
            ),
        )

        self.conn.commit()

    def handle_daily_picks(self, event: Dict[str, Any]):
        """
        Record daily picks generation events.
        """

        self._record_event("daily.picks.generated", event)

    def handle_trade_record(self, event: Dict[str, Any]):
        """
        Record trade events for later analysis.
        """

        self._record_event("trade.record.created", event)
