"""TopicRouter & schemas smoke tests."""
from __future__ import annotations

from datetime import date, datetime

from social_media_dispatcher import TopicRouter
from social_media_dispatcher.topic_router import _mask_name, _mask_symbol
from stock_analyzer.pipelines.daily_scan import DailyPicks, RecommendedStock


def _rec(symbol="600519", name="贵州茅台", bucket="aggressive") -> RecommendedStock:
    r = RecommendedStock(
        symbol=symbol, name=name, price=1500.0, change_pct=4.2,
        industry="白酒", rule_score=5.0, matched_rules=["not_st"],
    )
    r.bucket = bucket
    r.reasoning = "规则得分 5.0 + Agent BUY 置信 80%"
    r.agent_decision = "BUY"
    r.agent_confidence = 0.8
    r.agent_summary = "[mock] 看多"
    return r


def test_masking():
    assert _mask_symbol("600519") == "60xxxx"
    assert _mask_name("贵州茅台") == "贵X台"
    assert _mask_name("中") == "中X"
    assert _mask_name("") == "某股"


def test_from_daily_picks_full():
    picks = DailyPicks(
        pick_date=date(2026, 4, 26),
        is_mock_data=True,
        hot_sectors=["白酒", "AI算力", "低空经济"],
        aggressive=[_rec(name="贵州茅台", bucket="aggressive")],
        stable=[_rec(symbol="000858", name="五粮液", bucket="stable")],
    )
    payload = TopicRouter().from_daily_picks(picks, account_id="XHS_01")
    assert payload.account_id == "XHS_01"
    assert payload.kind == "daily_picks"
    assert "白酒" in payload.description
    assert len(payload.context.recommendations) == 2
    assert payload.context.recommendations[0].symbol_masked == "60xxxx"
    assert payload.context.recommendations[0].name_masked == "贵X台"
    # 确保未泄漏原代码/原名
    assert "600519" not in payload.model_dump_json()
    assert "贵州茅台" not in payload.model_dump_json()


def test_from_daily_picks_empty():
    picks = DailyPicks(
        pick_date=date(2026, 4, 26),
        is_mock_data=True,
        hot_sectors=[],
    )
    payload = TopicRouter().from_daily_picks(picks, account_id="XHS_01")
    assert "暂无明显主线" in payload.description


def test_from_trade_record():
    payload = TopicRouter().from_trade_record(
        record_id="abc123",
        safe_text="今天上车白酒板块龙头，关注20元附近",
        received_at=datetime(2026, 4, 26, 10, 30),
        account_id="XHS_01",
    )
    assert payload.kind == "trade_record"
    assert payload.context.trade_record is not None
    assert payload.context.trade_record.record_id == "abc123"


def test_from_manual():
    p = TopicRouter().from_manual("聊聊低空经济中线机会", account_id="XHS_02")
    assert p.kind == "manual"
    assert p.account_id == "XHS_02"
    assert "低空经济" in p.description
