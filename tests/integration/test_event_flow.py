import types

from ai_platform.central_brain.event_bus.event_bus import EventBus
from ai_platform.content_ai.topic_generation.topic_generator_agent import TopicGeneratorAgent


class DummyClient:
    def __init__(self):
        self.created = []

    def dispatch(self, payload):
        self.created.append(payload)


def test_daily_picks_event_triggers_topic_generation(monkeypatch):
    bus = EventBus()

    dummy = DummyClient()

    agent = TopicGeneratorAgent("http://dummy", "ACC")

    # replace SMA client
    agent.client = dummy

    def handler(event):
        agent.handle_daily_picks(event)

    bus.subscribe("daily.picks.generated", handler)

    bus.publish("daily.picks.generated", {"picks": {"items": []}})

    assert len(dummy.created) == 1
