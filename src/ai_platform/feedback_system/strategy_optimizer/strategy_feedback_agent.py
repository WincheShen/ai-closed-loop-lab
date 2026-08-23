from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Any


# 兼容测试 monkeypatch。生产不再创建/增长 strategy_metrics.sqlite。
DB_DIR = Path("data/strategy_feedback")
DB_PATH = DB_DIR / "strategy_metrics.sqlite"


class StrategyFeedbackAgent:
    """收集策略事件。默认只转发到 central_brain，不写独立库。"""

    def __init__(self):
        self.conn = None

    def _record_event(self, event_type: str, payload: Dict[str, Any]):
        try:
            from src.central_brain import get_central_brain
            get_central_brain().log_agent_event(
                payload.get("session_id", ""),
                "strategy_feedback",
                event_type,
                payload,
            )
        except Exception:
            pass

    def handle_daily_picks(self, event: Dict[str, Any]):
        """
        Record daily picks generation events.
        """

        self._record_event("daily.picks.generated", event)

    def handle_trade_record(self, event: Dict[str, Any]):
        """
        Record trade events for later analysis.
        """

        self._record_event("trade.record.created", event)
