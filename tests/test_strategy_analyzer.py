from ai_platform.feedback_system.strategy_optimizer.strategy_analyzer import StrategyAnalyzer
from ai_platform.feedback_system.strategy_optimizer.strategy_feedback_agent import StrategyFeedbackAgent


def test_strategy_feedback_does_not_create_sidecar_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "strategy"
    monkeypatch.setattr(
        "ai_platform.feedback_system.strategy_optimizer.strategy_feedback_agent.DB_DIR",
        db_dir,
    )
    monkeypatch.setattr(
        "ai_platform.feedback_system.strategy_optimizer.strategy_feedback_agent.DB_PATH",
        db_dir / "strategy_metrics.sqlite",
    )
    agent = StrategyFeedbackAgent()
    agent.handle_daily_picks({"date": "2026-01-01"})
    agent.handle_trade_record({"id": "t1"})
    assert not (db_dir / "strategy_metrics.sqlite").exists()


def test_strategy_analyzer_insights():
    analyzer = StrategyAnalyzer()
    insights = analyzer.simple_insights()
    assert "picks_to_trades_ratio" in insights
