from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_platform.central_brain.event_bus.event_monitor_api import app


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8010)
