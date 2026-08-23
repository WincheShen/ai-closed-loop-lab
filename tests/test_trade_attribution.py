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
    assert attr.strategy_id == "volume_breakout"


def test_resolve_strategy_id_normalizes_alias() -> None:
    from src.agents.memory.trade_attribution import resolve_strategy_id

    assert resolve_strategy_id({"original_strategy": "热点板块前排回踩"}) == "hot_sector_pullback"
    assert resolve_strategy_id({"strategy_id": "value_investing", "original_strategy": "x"}) == "value_investing"
    assert resolve_strategy_id({"original_strategy": "放量突破后第一次确认回踩"}) == "volume_breakout"


def test_repair_strategy_ids(tmp_path) -> None:
    import sqlite3

    from src.agents.memory.trade_attribution import repair_strategy_ids

    conn = sqlite3.connect(tmp_path / "t.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE positions (
            position_id TEXT, original_strategy TEXT, strategy_id TEXT
        );
        CREATE TABLE trade_attributions (
            position_id TEXT, strategy_id TEXT
        );
        INSERT INTO positions VALUES ('P1', '热点板块前排回踩', NULL);
        INSERT INTO trade_attributions VALUES ('P1', '');
        """
    )
    conn.commit()

    class Store:
        def _conn(self):
            return conn

        def _auto_commit(self):
            conn.commit()

    n = repair_strategy_ids(store=Store())
    assert n == 1
    pos = dict(conn.execute("SELECT * FROM positions").fetchone())
    attr = dict(conn.execute("SELECT * FROM trade_attributions").fetchone())
    assert pos["strategy_id"] == "hot_sector_pullback"
    assert attr["strategy_id"] == "hot_sector_pullback"
