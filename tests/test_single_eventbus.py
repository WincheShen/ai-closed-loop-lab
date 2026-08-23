"""生产路径只应有一套会写独立 sqlite 的 EventBus。"""
from __future__ import annotations

from pathlib import Path


def test_ai_platform_eventbus_has_no_sqlite_path() -> None:
    text = Path("src/ai_platform/central_brain/event_bus/event_bus.py").read_text()
    assert 'Path("data/event_bus")' not in text
    assert "sqlite3.connect" not in text
