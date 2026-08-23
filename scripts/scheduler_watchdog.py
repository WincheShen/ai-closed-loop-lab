#!/usr/bin/env python3
"""调度器自愈：心跳超时则重启容器，并 notify_ops。

建议在 NAS 上用 Task Scheduler / cron 每 5 分钟跑一次：
    python3 /volume1/docker/ai-lab/scripts/scheduler_watchdog.py

环境变量：
    WATCHDOG_STALE_SEC   心跳超时秒数，默认 600
    WATCHDOG_COOLDOWN_SEC 两次重启最短间隔，默认 900
    WATCHDOG_CONTAINER   默认 ai-lab-scheduler
    WATCHDOG_COMPOSE     默认 /volume1/docker/ai-lab/docker-compose.yml
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.infra.heartbeat import cooldown_ok, heartbeat_status
from src.infra.notify import notify_ops

DEFAULT_COMPOSE = "/volume1/docker/ai-lab/docker-compose.yml"
DOCKER = "/usr/local/bin/docker"
COMPOSE = "/usr/local/bin/docker-compose"


def _run(cmd: list[str], timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _docker(args: list[str], timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return _run([DOCKER, *args], timeout=timeout)


def restart_scheduler(container: str, compose: str) -> str:
    kill = _docker(["kill", container], timeout=20)
    if kill.returncode != 0:
        # daemon / inspect 卡死时 kill 也常挂；尝试 compose up
        pass
    up = _run(
        [COMPOSE, "-f", compose, "--profile", "scheduler", "up", "-d"],
        timeout=120,
    )
    if up.returncode != 0:
        raise RuntimeError((up.stderr or up.stdout or "compose up failed")[:400])
    return (up.stdout or "").strip() or "compose up -d"


def main() -> int:
    parser = argparse.ArgumentParser(description="scheduler heartbeat watchdog")
    parser.add_argument("--stale-sec", type=int, default=int(os.getenv("WATCHDOG_STALE_SEC", "600")))
    parser.add_argument("--cooldown-sec", type=int, default=int(os.getenv("WATCHDOG_COOLDOWN_SEC", "900")))
    parser.add_argument("--container", default=os.getenv("WATCHDOG_CONTAINER", "ai-lab-scheduler"))
    parser.add_argument("--compose", default=os.getenv("WATCHDOG_COMPOSE", DEFAULT_COMPOSE))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    status = heartbeat_status(stale_sec=args.stale_sec)
    if status["status"] == "ok":
        print(f"ok age={status['age_sec']}s")
        return 0

    marker = Path(status["path"]).with_name("watchdog.last_restart")
    if not cooldown_ok(marker, args.cooldown_sec):
        print(f"stale but cooling down status={status['status']} age={status['age_sec']}")
        return 0

    body = (
        f"status={status['status']} age={status['age_sec']} "
        f"path={status['path']} container={args.container}"
    )
    print(f"stale: {body}")
    if args.dry_run:
        notify_ops("scheduler 心跳超时 (dry-run)", body, level="warning")
        return 2

    try:
        detail = restart_scheduler(args.container, args.compose)
        marker.write_text(str(time.time()), encoding="utf-8")
        notify_ops("scheduler 心跳超时已重启", f"{body} restart={detail[:200]}", level="error")
        print("restarted")
        return 1
    except Exception as exc:
        notify_ops("scheduler 重启失败", f"{body} err={exc}", level="error")
        print(f"restart failed: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
