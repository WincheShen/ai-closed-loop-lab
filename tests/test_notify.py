"""notify_ops 落盘。"""
from __future__ import annotations

from src.infra import notify as notify_mod


def test_notify_ops_writes_log(tmp_path, monkeypatch) -> None:
    log = tmp_path / "ops.log"
    monkeypatch.setenv("OPS_ALERT_LOG", str(log))
    monkeypatch.delenv("OPS_WEBHOOK_URL", raising=False)
    notify_mod.notify_ops("t", "body")
    text = log.read_text(encoding="utf-8")
    assert "t" in text and "body" in text
