"""heartbeat + watchdog cooldown helpers。"""
from __future__ import annotations

import os
import time

from src.infra.heartbeat import cooldown_ok, heartbeat_status, start_heartbeat, write_heartbeat


def test_write_and_status_ok(tmp_path, monkeypatch) -> None:
    hb = tmp_path / "scheduler.heartbeat"
    monkeypatch.setenv("SCHEDULER_HEARTBEAT", str(hb))
    write_heartbeat()
    st = heartbeat_status(stale_sec=600)
    assert st["status"] == "ok"
    assert st["age_sec"] < 2


def test_independent_heartbeat_updates(tmp_path, monkeypatch) -> None:
    hb = tmp_path / "scheduler.heartbeat"
    monkeypatch.setenv("SCHEDULER_HEARTBEAT", str(hb))
    stopped, thread = start_heartbeat(interval_sec=0.02)
    try:
        deadline = time.time() + 1
        while not hb.exists() and time.time() < deadline:
            time.sleep(0.01)
        first = hb.stat().st_mtime_ns
        deadline = time.time() + 1
        while hb.stat().st_mtime_ns == first and time.time() < deadline:
            time.sleep(0.01)
        assert hb.stat().st_mtime_ns > first
    finally:
        stopped.set()
        thread.join(timeout=1)
    assert not thread.is_alive()


def test_missing_heartbeat(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SCHEDULER_HEARTBEAT", str(tmp_path / "nope"))
    st = heartbeat_status()
    assert st["status"] == "missing"


def test_stale_heartbeat(tmp_path, monkeypatch) -> None:
    hb = tmp_path / "scheduler.heartbeat"
    hb.write_text("old", encoding="utf-8")
    past = time.time() - 1200
    os.utime(hb, (past, past))
    monkeypatch.setenv("SCHEDULER_HEARTBEAT", str(hb))
    st = heartbeat_status(stale_sec=600)
    assert st["status"] == "stale"


def test_cooldown(tmp_path) -> None:
    marker = tmp_path / "watchdog.last_restart"
    assert cooldown_ok(marker, 900)
    marker.write_text("x", encoding="utf-8")
    assert not cooldown_ok(marker, 900)
    past = time.time() - 1000
    os.utime(marker, (past, past))
    assert cooldown_ok(marker, 900)
