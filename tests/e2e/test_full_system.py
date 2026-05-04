from ai_platform.central_brain.event_bus.event_bus import EventBus
from ai_platform.content_ai.topic_generation.topic_generator_agent import TopicGeneratorAgent
from ai_platform.feedback_system.strategy_optimizer.strategy_feedback_agent import StrategyFeedbackAgent


class DummyClient:
    def __init__(self):
        self.tasks = []

    def dispatch(self, payload):
        self.tasks.append(payload)


def test_full_event_cycle():
    bus = EventBus()

    topic_agent = TopicGeneratorAgent("http://dummy", "ACC")
    topic_agent.client = DummyClient()

    feedback_agent = StrategyFeedbackAgent()

    bus.subscribe("daily.picks.generated", topic_agent.handle_daily_picks)
    bus.subscribe("daily.picks.generated", feedback_agent.handle_daily_picks)

    bus.publish("daily.picks.generated", {"picks": {"items": []}})

    assert len(topic_agent.client.tasks) == 1
