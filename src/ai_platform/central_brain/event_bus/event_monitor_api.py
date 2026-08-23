from __future__ import annotations

import json
from typing import List

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="AI Lab Event Monitor", version="0.2")


class EventRecord(BaseModel):
    id: str
    event_type: str
    created_at: str
    payload: dict


@app.get("/health")
def health():
    return {"status": "ok", "service": "event_monitor", "source": "central_brain.db"}


@app.get("/events/recent", response_model=List[EventRecord])
def recent_events(limit: int = 20):
    try:
        from src.central_brain import get_central_brain
        conn = get_central_brain().store._conn()
        rows = conn.execute(
            """SELECT event_id, event_type, payload, created_at
               FROM events ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    except Exception:
        return []

    out = []
    for r in rows:
        try:
            payload = json.loads(r[2]) if r[2] else {}
        except Exception:
            payload = {}
        out.append(
            EventRecord(
                id=str(r[0]),
                event_type=r[1] or "",
                created_at=r[3] or "",
                payload=payload,
            )
        )
    return out
