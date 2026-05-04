from __future__ import annotations

from ai_platform.central_brain.event_bus.event_bus import EventBus

from .trade_content_agent import TradeContentAgent


def register_trade_listener(event_bus: EventBus, sma_url: str, account_id: str):
    agent = TradeContentAgent(sma_url, account_id)

    def handler(event):
        agent.handle_trade_record(event)

    event_bus.subscribe("trade.record.created", handler)
