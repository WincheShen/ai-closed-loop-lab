from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_platform.central_brain.event_bus.event_bus import get_event_bus
from ai_platform.central_brain.workflow_engine.workflow_engine import WorkflowEngine
from ai_platform.central_brain.workflow_engine.daily_market_workflow import workflow

logging.basicConfig(level=logging.INFO)


def main():
    event_bus = get_event_bus()

    engine = WorkflowEngine()

    engine.register(workflow)

    engine.run(
        "daily_market_workflow",
        {
            "event_bus": event_bus,
            "trading_agent_url": "http://localhost:8001",
        },
    )


if __name__ == "__main__":
    main()
