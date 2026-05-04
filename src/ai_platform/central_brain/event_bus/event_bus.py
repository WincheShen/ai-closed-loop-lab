from __future__ import annotations

import json
import threading
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Any, Optional


class InMemoryEventBus:
    """
    Simple in-memory pub/sub bus.

    Used as a fallback when Redis is not available.
    """

    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, handler: Callable[[Dict[str, Any]], None]):
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(handler)

    def publish(self, event_type: str, payload: Dict[str, Any]):
        handlers = self._subscribers.get(event_type, [])
        for handler in handlers:
            handler(payload)


class RedisStreamEventBus:
    """
    Redis Streams based event bus.

    NOTE: Minimal prototype. Consumer groups and persistence
    management will be added later.
    """

    def __init__(self, redis_client, stream_prefix: str = "acl:"):
        self.redis = redis_client
        self.stream_prefix = stream_prefix

    def _stream(self, event_type: str) -> str:
        return f"{self.stream_prefix}{event_type}"

    def publish(self, event_type: str, payload: Dict[str, Any]):
        stream = self._stream(event_type)
        self.redis.xadd(stream, {"data": json.dumps(payload)})

    def subscribe(self, event_type: str, handler: Callable[[Dict[str, Any]], None]):
        raise NotImplementedError(
            "Redis stream consumer not implemented yet."
        )


class EventBus:
    """
    Unified EventBus abstraction with SQLite event log.

    Events are persisted before dispatching to subscribers.
    """

    def __init__(self, redis_client: Optional[Any] = None):
        if redis_client:
            self.impl = RedisStreamEventBus(redis_client)
        else:
            self.impl = InMemoryEventBus()

        db_dir = Path("data/event_bus")
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = db_dir / "events.sqlite"

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def _log_event(self, event_type: str, payload: Dict[str, Any]):
        self._conn.execute(
            "INSERT INTO events (event_type, payload_json, created_at) VALUES (?, ?, ?)",
            (
                event_type,
                json.dumps(payload, ensure_ascii=False),
                datetime.utcnow().isoformat(),
            ),
        )
        self._conn.commit()

    def publish(self, event_type: str, payload: Dict[str, Any]):
        self._log_event(event_type, payload)
        self.impl.publish(event_type, payload)

    def subscribe(self, event_type: str, handler: Callable[[Dict[str, Any]], None]):
        if hasattr(self.impl, "subscribe"):
            self.impl.subscribe(event_type, handler)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_instance: Optional["EventBus"] = None
_instance_lock = threading.Lock()


def get_event_bus(redis_client: Optional[Any] = None) -> "EventBus":
    """Return the process-wide EventBus singleton.

    All producers (webhook_listener, daily_workflow, ...) and consumers
    (TopicGeneratorAgent, StrategyFeedbackAgent, ...) must share one bus
    instance, otherwise subscribe/publish happen on different objects and
    no handler ever fires.

    The ``redis_client`` argument is only honoured on the first call; later
    calls return the existing singleton regardless of arguments.
    """
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = EventBus(redis_client=redis_client)
    return _instance


def reset_event_bus() -> None:
    """Drop the singleton. Intended for tests only."""
    global _instance
    with _instance_lock:
        _instance = None
