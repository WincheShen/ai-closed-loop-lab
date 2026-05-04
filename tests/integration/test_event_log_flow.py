import sqlite3
from pathlib import Path

from ai_platform.central_brain.event_bus.event_bus import EventBus


def test_event_log_written(tmp_path, monkeypatch):
    db_dir = tmp_path / "event_bus"
    db_dir.mkdir()

    monkeypatch.setattr(
        "ai_platform.central_brain.event_bus.event_bus.Path",
        lambda p="": db_dir if p == "data/event_bus" else Path(p),
    )

    bus = EventBus()

    bus.publish("test.integration", {"value": 1})

    db = db_dir / "events.sqlite"

    conn = sqlite3.connect(db)

    rows = conn.execute("SELECT event_type FROM events").fetchall()

    assert rows[0][0] == "test.integration"
