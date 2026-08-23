"""兼容层：旧 ai_platform.EventBus 不再写 events.sqlite。

生产事件只进 central_brain.db。本类只做进程内 pub/sub，供尚未迁移的测试使用。
新代码请用 ``src.central_brain.EventBus`` / ``get_central_brain().bus``。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class InMemoryEventBus:
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


class EventBus:
    """进程内转发。不再创建 data/event_bus/events.sqlite。"""

    def __init__(self, redis_client: Optional[Any] = None):
        if redis_client:
            logger.warning("ai_platform EventBus Redis 路径已废弃，回退进程内总线")
        self.impl = InMemoryEventBus()
        self._db_path = None

    def publish(self, event_type: str, payload: Dict[str, Any]):
        # 顺带写入 central_brain（失败不挡内存分发）
        try:
            from src.central_brain import get_central_brain
            get_central_brain().bus.publish(event_type, payload)
        except Exception:
            logger.debug("central_brain publish skipped", exc_info=True)
        self.impl.publish(event_type, payload)

    def subscribe(self, event_type: str, handler: Callable[[Dict[str, Any]], None]):
        self.impl.subscribe(event_type, handler)


_instance: Optional[EventBus] = None
_instance_lock = threading.Lock()


def get_event_bus(redis_client: Optional[Any] = None) -> EventBus:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = EventBus(redis_client=redis_client)
    return _instance


def reset_event_bus() -> None:
    global _instance
    with _instance_lock:
        _instance = None
