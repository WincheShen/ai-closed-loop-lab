from src.agents.strategist.signal_generator import StrategistEngine


class DummyStore:
    def get_recent_lessons(self, regime=None, limit=3):
        return [
            {"symbol": "000001", "lesson_text": "avoid chasing highs"},
            {"symbol": "000002", "lesson_text": "respect stop loss"},
        ]


class DummyBrain:
    def __init__(self):
        self.store = DummyStore()


def test_lessons_block(monkeypatch):
    from src.agents.strategist import signal_generator as module

    monkeypatch.setattr(module, "get_central_brain", lambda: DummyBrain())

    engine = StrategistEngine(
        session_id="test",
        hot_sectors=[],
        persona=None,
        market_regime={"regime": "bull"},
    )

    block = engine._lessons_block()

    assert "历史交易教训" in block
    assert "avoid chasing highs" in block
    assert "respect stop loss" in block
```