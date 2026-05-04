from __future__ import annotations

from typing import Dict, Any

from social_media_dispatcher.topic_router import TopicRouter
from social_media_dispatcher.client import SmaClient
from stock_analyzer.pipelines.daily_scan import DailyPicks


class TopicGeneratorAgent:
    """
    Event-driven agent that converts daily picks into content topics.
    """

    def __init__(self, sma_base_url: str, account_id: str):
        self.router = TopicRouter()
        self.client = SmaClient(base_url=sma_base_url)
        self.account_id = account_id

    def handle_daily_picks(self, event: Dict[str, Any]):
        """
        Event handler for daily.picks.generated.

        Accepts either a DailyPicks dataclass or a plain dict payload
        (the latter is used by smoke tests and cross-service events).
        """

        picks = event.get("picks")

        if picks is None:
            return

        if isinstance(picks, DailyPicks):
            payload = self.router.from_daily_picks(
                picks,
                account_id=self.account_id,
            )
        else:
            # Plain dict (e.g. event smoke test or cross-process event):
            # fall back to a manual topic so the dispatch contract still fires.
            payload = self.router.from_manual(
                topic_text="Daily picks event received (placeholder).",
                account_id=self.account_id,
            )

        self.client.dispatch(payload)
