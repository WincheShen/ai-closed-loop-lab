"""运维告警 — 先落盘，可选 webhook。

没有 OPS_WEBHOOK_URL 时只写 logs/ops_alerts.log，避免绑死某一家 IM。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _alert_log_path() -> Path:
    custom = os.getenv("OPS_ALERT_LOG")
    if custom:
        return Path(custom)
    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        return Path(data_dir) / "logs" / "ops_alerts.log"
    return _PROJECT_ROOT / "logs" / "ops_alerts.log"


def notify_ops(title: str, body: str, *, level: str = "warning") -> None:
    """写一条告警。失败不得抛给调用方。"""
    ts = datetime.now().isoformat(timespec="seconds")
    line = f"{ts} [{level.upper()}] {title} | {body}\n"
    path = _alert_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        logger.warning("ops alert 落盘失败: %s", exc)

    logger.warning("[OPS] %s — %s", title, body)

    url = os.getenv("OPS_WEBHOOK_URL", "").strip()
    if not url:
        return
    payload = json.dumps(
        {"title": title, "body": body, "level": level, "ts": ts},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("ops webhook 失败: %s", exc)
