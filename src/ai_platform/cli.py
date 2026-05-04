from __future__ import annotations

import subprocess
import sys


def _run(cmd: list[str]):
    proc = subprocess.run(cmd)
    sys.exit(proc.returncode)


def main():
    if len(sys.argv) < 2:
        print("Usage: ai-lab {webhook|workflow|monitor|metrics|dev}")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "webhook":
        _run(["uvicorn", "webhook_listener.server:app", "--host", "0.0.0.0", "--port", "8002"])

    elif cmd == "workflow":
        from scripts.run_daily_workflow import main as workflow_main

        workflow_main()

    elif cmd == "monitor":
        _run(["uvicorn", "ai_platform.central_brain.event_bus.event_monitor_api:app", "--host", "0.0.0.0", "--port", "8010"])

    elif cmd == "metrics":
        _run(["uvicorn", "ai_platform.feedback_system.strategy_optimizer.strategy_metrics_api:app", "--host", "0.0.0.0", "--port", "8011"])

    elif cmd == "dev":
        _run(["bash", "scripts/dev", *sys.argv[2:]])

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
