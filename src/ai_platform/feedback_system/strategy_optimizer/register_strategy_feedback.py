from __future__ import annotations

from ai_platform.central_brain.event_bus.event_bus import EventBus

from .strategy_feedback_agent import StrategyFeedbackAgent


def register_strategy_feedback(event_bus: EventBus):
    agent = StrategyFeedbackAgent()

    def picks_handler(event):
        agent.handle_daily_picks(event)

    def trade_handler(event):
        agent.handle_trade_record(event)

    event_bus.subscribe("daily.picks.generated", picks_handler)
    event_bus.subscribe("trade.record.created", trade_handler)
