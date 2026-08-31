"""调度器心跳路径与状态。"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def heartbeat_path() -> Path:
    custom = os.getenv("SCHEDULER_HEARTBEAT")
    if custom:
        return Path(custom)
    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        return Path(data_dir) / "scheduler.heartbeat"
    if os.getenv("DB_PATH"):
        return Path("/app/data/scheduler.heartbeat")
    return _PROJECT_ROOT / "data" / "scheduler.heartbeat"


def heartbeat_age_sec(path: Path | None = None) -> float | None:
    p = path or heartbeat_path()
    try:
        return time.time() - p.stat().st_mtime
    except OSError:
        return None


def heartbeat_status(*, stale_sec: int | None = None) -> dict[str, Any]:
    path = heartbeat_path()
    limit = stale_sec if stale_sec is not None else int(os.getenv("WATCHDOG_STALE_SEC", "600"))
    age = heartbeat_age_sec(path)
    if age is None:
        return {
            "status": "missing",
            "path": str(path),
            "age_sec": None,
            "stale_sec": limit,
        }
    return {
        "status": "ok" if age < limit else "stale",
        "path": str(path),
        "age_sec": round(age, 1),
        "stale_sec": limit,
        "mtime": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
    }


def write_heartbeat(path: Path | None = None) -> Path:
    p = path or heartbeat_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    return p


def start_heartbeat(interval_sec: float = 60) -> tuple[threading.Event, threading.Thread]:
    stopped = threading.Event()

    def run() -> None:
        while not stopped.is_set():
            try:
                write_heartbeat()
            except OSError:
                pass
            stopped.wait(interval_sec)

    thread = threading.Thread(target=run, name="scheduler-heartbeat", daemon=True)
    thread.start()
    return stopped, thread


def cooldown_ok(marker: Path, cooldown_sec: int) -> bool:
    try:
        age = time.time() - marker.stat().st_mtime
    except OSError:
        return True
    return age >= cooldown_sec
