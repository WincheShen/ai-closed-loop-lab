from datetime import datetime

from src.agents.memory.trade_attribution import TradeAttributor


class DummyBrainStore:
    def latest_market_regime(self):
        return {"regime": "bull"}

    def save_attribution(self, data):
        pass

    def save_lesson(self, data):
        pass


class DummyBrain:
    def __init__(self):
        self.store = DummyBrainStore()


def test_attribute_basic(monkeypatch):
    from src.agents.memory import trade_attribution as module

    monkeypatch.setattr(module, "get_central_brain", lambda: DummyBrain())

    def fake_llm_generate(self, attr, position):
        return {
            "actual_narrative": "price moved up after entry",
            "lesson": "follow the trend",
            "should_have": "hold longer",
            "tags": ["trend"],
        }

    monkeypatch.setattr(module.TradeAttributor, "_llm_generate", fake_llm_generate)

    position = {
        "position_id": "POS1",
        "symbol": "000001",
        "name": "PingAn",
        "entry_price": 10.0,
        "close_price": 11.0,
        "realized_pnl": 100,
        "entry_date": datetime.now().isoformat(),
        "closed_at": datetime.now().isoformat(),
        "original_thesis": "breakout",
        "original_strategy": "volume_breakout",
        "bull_case": "strong trend",
        "bear_case": "false breakout",
        "market_regime": "bull",
        "target_price": 11.5,
        "stop_loss": 9.5,
        "current_qty": 100,
    }

    attr = TradeAttributor("test").attribute(position)

    assert attr.symbol == "000001"
    assert attr.outcome in ("win", "loss", "breakeven")
    assert attr.primary_cause != ""
    assert attr.lesson == "follow the trend"
