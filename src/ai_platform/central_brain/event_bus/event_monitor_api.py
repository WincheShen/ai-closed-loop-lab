from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List

from fastapi import FastAPI
from pydantic import BaseModel


DB_PATH = Path("data/event_bus/events.sqlite")

app = FastAPI(title="AI Lab Event Monitor", version="0.1")


class EventRecord(BaseModel):
    id: int
    event_type: str
    created_at: str
    payload: dict


@app.get("/health")
def health():
    return {"status": "ok", "service": "event_monitor"}


@app.get("/events/recent", response_model=List[EventRecord])
def recent_events(limit: int = 20):

    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH)

    rows = conn.execute(
        """
        SELECT id, event_type, payload_json, created_at
        FROM events
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    events = []

    for r in rows:
        try:
            payload = json.loads(r[2])
        except Exception:
            payload = {}

        events.append(
            EventRecord(
                id=r[0],
                event_type=r[1],
                created_at=r[3],
                payload=payload,
            )
        )

    return events
