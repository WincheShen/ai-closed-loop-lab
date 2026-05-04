from __future__ import annotations

from ai_platform.central_brain.event_bus.event_bus import EventBus

from .topic_generator_agent import TopicGeneratorAgent


def register_topic_listener(event_bus: EventBus, sma_url: str, account_id: str):
    agent = TopicGeneratorAgent(sma_url, account_id)

    def handler(event):
        agent.handle_daily_picks(event)

    event_bus.subscribe("daily.picks.generated", handler)
