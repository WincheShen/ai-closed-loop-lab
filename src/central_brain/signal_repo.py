"""trade_signals 读写 — MemoryStore mixin，不改变调用方 API。"""
from __future__ import annotations

from typing import Any


class SignalRepoMixin:
    def save_trade_signal(self, session_id: str, signal: dict) -> None:
        raise NotImplementedError("provided by MemoryStore")

    def list_active_signals(self, session_id: str | None = None) -> list[dict]:
        raise NotImplementedError("provided by MemoryStore")

    def list_pending_signals(self) -> list[dict]:
        raise NotImplementedError("provided by MemoryStore")

    def update_signal_status(self, signal_id: str, status: str) -> None:
        raise NotImplementedError("provided by MemoryStore")

    def list_signals_by_status(self, status: str, limit: int = 200) -> list[dict[str, Any]]:
        conn = self._conn()  # type: ignore[attr-defined]
        rows = conn.execute(
            "SELECT * FROM trade_signals WHERE status = ? ORDER BY timestamp DESC LIMIT ?",
            (status, limit),
        ).fetchall()
        return [dict(r) for r in rows]
