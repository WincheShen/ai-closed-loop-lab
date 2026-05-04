from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, Any, List

DB_PATH = Path("data/strategy_feedback/strategy_metrics.sqlite")


class StrategyAnalyzer:
    """
    Simple analytics layer on top of strategy_events.

    Reads recorded events and produces lightweight insights
    that can later feed reports or LLM analysis.
    """

    def __init__(self):
        if not DB_PATH.exists():
            self.conn = None
        else:
            self.conn = sqlite3.connect(DB_PATH)

    def _load_events(self) -> List[Dict[str, Any]]:
        if self.conn is None:
            return []

        rows = self.conn.execute(
            "SELECT event_type, payload_json, created_at FROM strategy_events"
        ).fetchall()

        events: List[Dict[str, Any]] = []

        for r in rows:
            try:
                payload = json.loads(r[1])
            except Exception:
                payload = {}

            events.append(
                {
                    "event_type": r[0],
                    "payload": payload,
                    "created_at": r[2],
                }
            )

        return events

    def basic_stats(self) -> Dict[str, Any]:
        events = self._load_events()

        picks = 0
        trades = 0

        for e in events:
            if e["event_type"] == "daily.picks.generated":
                picks += 1
            elif e["event_type"] == "trade.record.created":
                trades += 1

        return {
            "total_events": len(events),
            "total_picks": picks,
            "total_trades": trades,
        }

    def simple_insights(self) -> Dict[str, Any]:
        stats = self.basic_stats()

        picks = stats["total_picks"]
        trades = stats["total_trades"]

        if picks == 0:
            conversion = 0.0
        else:
            conversion = trades / picks

        return {
            "picks_to_trades_ratio": round(conversion, 3),
            "interpretation": (
                "low" if conversion < 0.2 else "medium" if conversion < 0.5 else "high"
            ),
        }
