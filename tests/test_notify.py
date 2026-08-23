"""notify_ops 落盘 + webhook 协议。"""
from __future__ import annotations

import json

from src.infra import notify as notify_mod


def test_notify_ops_writes_log(tmp_path, monkeypatch) -> None:
    log = tmp_path / "ops.log"
    monkeypatch.setenv("OPS_ALERT_LOG", str(log))
    monkeypatch.delenv("OPS_WEBHOOK_URL", raising=False)
    notify_mod.notify_ops("t", "body")
    text = log.read_text(encoding="utf-8")
    assert "t" in text and "body" in text
    jsonl = log.with_suffix(".jsonl")
    rec = json.loads(jsonl.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["title"] == "t" and rec["body"] == "body"


def test_detect_webhook_kind() -> None:
    assert notify_mod.detect_webhook_kind("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x") == "wecom"
    assert notify_mod.detect_webhook_kind("https://open.feishu.cn/open-apis/bot/v2/hook/abc") == "feishu"
    assert notify_mod.detect_webhook_kind("https://oapi.dingtalk.com/robot/send?access_token=x") == "dingtalk"
    assert notify_mod.detect_webhook_kind("https://api.day.app/KEY") == "bark"
    assert notify_mod.detect_webhook_kind("https://hooks.example.com/x") == "generic"


def test_build_wecom_payload() -> None:
    method, body, ctype = notify_mod.build_webhook_payload(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x",
        "title", "body", level="error", ts="t",
    )
    assert method == "POST" and ctype == "application/json"
    payload = json.loads(body)
    assert payload["msgtype"] == "text"
    assert "title" in payload["text"]["content"]


def test_build_bark_get() -> None:
    method, _body, extra = notify_mod.build_webhook_payload(
        "https://api.day.app/KEY",
        "hard_scan 失败", "persona=x", level="warning", ts="t",
    )
    assert method == "GET"
    assert extra.startswith("https://api.day.app/KEY/")


def test_recent_alerts(tmp_path, monkeypatch) -> None:
    log = tmp_path / "ops.log"
    monkeypatch.setenv("OPS_ALERT_LOG", str(log))
    monkeypatch.delenv("OPS_WEBHOOK_URL", raising=False)
    notify_mod.notify_ops("a1", "b1")
    notify_mod.notify_ops("a2", "b2")
    recs = notify_mod.recent_alerts(limit=10)
    assert [r["title"] for r in recs] == ["a2", "a1"]


def test_webhook_post_called(tmp_path, monkeypatch) -> None:
    log = tmp_path / "ops.log"
    monkeypatch.setenv("OPS_ALERT_LOG", str(log))
    monkeypatch.setenv("OPS_WEBHOOK_URL", "https://hooks.example.com/x")
    seen: list[object] = []

    class _Resp:
        def read(self) -> bytes:
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=5):
        seen.append((req.full_url, req.data))
        return _Resp()

    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", fake_urlopen)
    notify_mod.notify_ops("t", "body")
    assert seen
    url, data = seen[0]
    assert url == "https://hooks.example.com/x"
    assert b"title" in data
