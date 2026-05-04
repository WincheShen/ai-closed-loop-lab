from ai_platform.feedback_system.strategy_optimizer.strategy_analyzer import StrategyAnalyzer
from ai_platform.feedback_system.strategy_optimizer.strategy_feedback_agent import StrategyFeedbackAgent


def test_strategy_analyzer_basic_stats(tmp_path, monkeypatch):
    db_dir = tmp_path / "strategy"
    db_dir.mkdir()

    monkeypatch.setattr(
        "ai_platform.feedback_system.strategy_optimizer.strategy_feedback_agent.DB_DIR",
        db_dir,
    )

    agent = StrategyFeedbackAgent()

    agent.handle_daily_picks({"date": "2026-01-01"})
    agent.handle_trade_record({"id": "t1"})

    analyzer = StrategyAnalyzer()

    stats = analyzer.basic_stats()

    assert stats["total_picks"] >= 1
    assert stats["total_trades"] >= 1


def test_strategy_analyzer_insights():
    analyzer = StrategyAnalyzer()

    insights = analyzer.simple_insights()

    assert "picks_to_trades_ratio" in insights
