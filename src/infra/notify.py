"""运维告警 — 先落盘，再按 URL 推到 IM。

保证通道：logs/ops_alerts.log + logs/ops_alerts.jsonl（/api/ops/alerts 可读）。
可选：OPS_WEBHOOK_URL，按地址自动识别企业微信 / 飞书 / 钉钉 / Bark / 通用 JSON。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


def _alert_jsonl_path() -> Path:
    custom = os.getenv("OPS_ALERT_JSONL")
    if custom:
        return Path(custom)
    return _alert_log_path().with_suffix(".jsonl")


def detect_webhook_kind(url: str, explicit: str = "") -> str:
    kind = (explicit or os.getenv("OPS_WEBHOOK_KIND", "auto")).strip().lower()
    if kind and kind != "auto":
        return kind
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    if "qyapi.weixin.qq.com" in host:
        return "wecom"
    if "open.feishu.cn" in host or "open.larksuite.com" in host:
        return "feishu"
    if "oapi.dingtalk.com" in host:
        return "dingtalk"
    if "api.day.app" in host or host.startswith("bark.") or ".bark." in host:
        return "bark"
    return "generic"


def build_webhook_payload(
    url: str, title: str, body: str, *, level: str, ts: str, kind: str = "",
) -> tuple[str, bytes, str]:
    """返回 (method, body, content_type)。Bark 可能改成 GET。"""
    vendor = detect_webhook_kind(url, kind)
    text = f"[{level.upper()}] {title}\n{body}"
    if vendor == "wecom":
        payload = {"msgtype": "text", "text": {"content": text}}
    elif vendor == "feishu":
        payload = {"msg_type": "text", "content": {"text": text}}
    elif vendor == "dingtalk":
        payload = {"msgtype": "text", "text": {"content": text}}
    elif vendor == "bark":
        # Bark 官方：GET /{key}/{title}/{body}
        parsed = urlparse(url)
        if parsed.path.rstrip("/").count("/") >= 1 and not parsed.query:
            title_q = urllib.parse.quote(title, safe="")
            body_q = urllib.parse.quote(body, safe="")
            get_url = url.rstrip("/") + f"/{title_q}/{body_q}"
            return "GET", b"", get_url
        payload = {"title": title, "body": body, "level": level}
    else:
        payload = {"title": title, "body": body, "level": level, "ts": ts, "text": text}
    return "POST", json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json"


def recent_alerts(limit: int = 50) -> list[dict[str, Any]]:
    path = _alert_jsonl_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-max(limit, 1):]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.reverse()
    return out


def notify_ops(title: str, body: str, *, level: str = "warning") -> None:
    """写一条告警。失败不得抛给调用方。"""
    ts = datetime.now().isoformat(timespec="seconds")
    line = f"{ts} [{level.upper()}] {title} | {body}\n"
    path = _alert_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        record = {"ts": ts, "level": level, "title": title, "body": body}
        with _alert_jsonl_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("ops alert 落盘失败: %s", exc)

    logger.warning("[OPS] %s — %s", title, body)

    url = os.getenv("OPS_WEBHOOK_URL", "").strip()
    if not url:
        return
    method, payload, extra = build_webhook_payload(url, title, body, level=level, ts=ts)
    try:
        if method == "GET":
            req = urllib.request.Request(extra, method="GET")
        else:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": extra},
                method="POST",
            )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("ops webhook 失败: %s", exc)
