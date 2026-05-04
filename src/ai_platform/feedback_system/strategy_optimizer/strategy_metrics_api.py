from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, Any

from fastapi import FastAPI
from .strategy_analyzer import StrategyAnalyzer


DB_PATH = Path("data/strategy_feedback/strategy_metrics.sqlite")

app = FastAPI(title="Strategy Metrics API", version="0.1")


def _connect():
    if not DB_PATH.exists():
        return None
    return sqlite3.connect(DB_PATH)


@app.get("/health")
def health():
    return {"status": "ok", "service": "strategy_metrics"}


@app.get("/strategy/summary")
def strategy_summary() -> Dict[str, Any]:

    conn = _connect()

    if conn is None:
        return {
            "total_events": 0,
            "daily_picks": 0,
            "trade_records": 0,
        }

    rows = conn.execute(
        "SELECT event_type, payload_json FROM strategy_events"
    ).fetchall()

    total_events = len(rows)
    daily_picks = 0
    trade_records = 0

    for r in rows:
        event_type = r[0]

        if event_type == "daily.picks.generated":
            daily_picks += 1

        elif event_type == "trade.record.created":
            trade_records += 1

    return {
        "total_events": total_events,
        "daily_picks": daily_picks,
        "trade_records": trade_records,
    }


@app.get("/strategy/analyzer/stats")
def analyzer_stats():
    analyzer = StrategyAnalyzer()
    return analyzer.basic_stats()


@app.get("/strategy/analyzer/insights")
def analyzer_insights():
    analyzer = StrategyAnalyzer()
    return analyzer.simple_insights()


@app.get("/strategy/recent_trades")
def recent_trades(limit: int = 10):

    conn = _connect()

    if conn is None:
        return []

    rows = conn.execute(
        """
        SELECT payload_json, created_at
        FROM strategy_events
        WHERE event_type = 'trade.record.created'
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    trades = []

    for r in rows:
        try:
            payload = json.loads(r[0])
        except Exception:
            payload = {}

        trades.append({
            "created_at": r[1],
            "payload": payload,
        })

    return trades
