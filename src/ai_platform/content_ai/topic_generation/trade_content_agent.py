from __future__ import annotations

from datetime import datetime
from typing import Dict, Any

from social_media_dispatcher.topic_router import TopicRouter
from social_media_dispatcher.client import SmaClient


class TradeContentAgent:
    """
    Agent that converts trade records into publishable content topics.
    """

    def __init__(self, sma_base_url: str, account_id: str):
        self.router = TopicRouter()
        self.client = SmaClient(base_url=sma_base_url)
        self.account_id = account_id

    def handle_trade_record(self, event: Dict[str, Any]):
        """
        Event handler for trade.record.created.
        """

        safe_text = event.get("safe_text")

        if not safe_text:
            return

        received_at = event.get("received_at") or datetime.now()
        if isinstance(received_at, str):
            try:
                received_at = datetime.fromisoformat(received_at)
            except ValueError:
                received_at = datetime.now()

        payload = self.router.from_trade_record(
            record_id=str(event.get("record_id", "")),
            safe_text=safe_text,
            received_at=received_at,
            account_id=self.account_id,
            redacted_image_url=event.get("redacted_image_path"),
        )

        self.client.dispatch(payload)
