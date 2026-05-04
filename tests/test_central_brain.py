"""Tests for Central Brain metadata store."""

from __future__ import annotations

import pytest

from src.central_brain import get_central_brain
from src.graph.state import create_empty_state


def test_central_brain_singleton() -> None:
    brain1 = get_central_brain()
    brain2 = get_central_brain()
    assert brain1 is brain2


def test_save_and_load_session(tmp_path) -> None:
    # 使用临时数据库
    import os
    from src.central_brain.metadata_store import MemoryStore

    db_path = str(tmp_path / "test.db")
    store = MemoryStore(db_path)

    state = create_empty_state("test-save", "mock")
    state["hot_sectors"] = ["低空经济"]

    store.save_session("test-save", "mock", state)
    loaded = store.load_session("test-save")

    assert loaded is not None
    assert loaded["session_id"] == "test-save"
    assert loaded["hot_sectors"] == ["低空经济"]


def test_event_logging(tmp_path) -> None:
    from src.central_brain.metadata_store import MemoryStore

    db_path = str(tmp_path / "test2.db")
    store = MemoryStore(db_path)

    store.log_event("s1", "explorer", "scan_complete", {"count": 50})
    events = store.query_events(session_id="s1", agent="explorer")
    assert len(events) == 1
    assert events[0]["event_type"] == "scan_complete"
