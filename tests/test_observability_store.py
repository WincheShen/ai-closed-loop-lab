"""Tests for Phase 3.5 observability tables on MemoryStore.

Covers:
- daily_picks_archive  (save_daily_pick / get_daily_pick)
- social_posts         (record_social_post / update_social_post_metrics / list_social_posts)
- llm_calls            (record_llm_call / llm_cost_summary)
"""

from __future__ import annotations

from src.central_brain.metadata_store import MemoryStore


def _fresh_store(tmp_path) -> MemoryStore:
    return MemoryStore(str(tmp_path / "obs.db"))


def test_daily_pick_save_and_load(tmp_path):
    store = _fresh_store(tmp_path)

    store.save_daily_pick(
        pick_date="2026-04-28",
        is_mock_data=False,
        hot_sectors=["半导体", "AI算力"],
        aggressive=[{"symbol": "600519", "score": 6.0}],
        stable=[{"symbol": "601318", "score": 5.0}],
        candidates_count=30,
        agent_calls_count=3,
        total_llm_cost_usd=0.42,
        elapsed_seconds=1500.0,
        picks_file_path="data/daily_picks/2026-04-28.json",
    )

    row = store.get_daily_pick("2026-04-28")
    assert row is not None
    assert row["is_mock_data"] == 0
    assert row["candidates_count"] == 30
    assert row["agent_calls_count"] == 3
    assert round(row["total_llm_cost_usd"], 2) == 0.42
    assert "半导体" in row["hot_sectors_json"]
    assert "600519" in row["aggressive_json"]


def test_daily_pick_upsert(tmp_path):
    store = _fresh_store(tmp_path)

    store.save_daily_pick(
        pick_date="2026-04-28",
        is_mock_data=True,
        hot_sectors=[],
        aggressive=[],
        stable=[],
    )
    store.save_daily_pick(
        pick_date="2026-04-28",
        is_mock_data=False,
        hot_sectors=["半导体"],
        aggressive=[{"symbol": "600519"}],
        stable=[],
        candidates_count=10,
    )

    row = store.get_daily_pick("2026-04-28")
    assert row["is_mock_data"] == 0
    assert row["candidates_count"] == 10


def test_social_post_lifecycle(tmp_path):
    store = _fresh_store(tmp_path)

    store.record_social_post(
        sma_task_id="task-001",
        account_id="XHS_01",
        platform="xhs",
        source_pick_date="2026-04-28",
        source_symbols=["600519", "601318"],
        topic="半导体方向观察",
    )

    posts = store.list_social_posts(account_id="XHS_01")
    assert len(posts) == 1
    assert posts[0]["sma_task_id"] == "task-001"
    assert posts[0]["sma_status"] == "pending"
    assert "600519" in posts[0]["source_symbols_json"]

    store.update_social_post_metrics(
        sma_task_id="task-001",
        sma_status="completed",
        post_url="https://xhs.example/abc",
        published_at="2026-04-28T20:00:00",
        last_metrics={"likes": 42, "comments": 5},
    )

    posts = store.list_social_posts(account_id="XHS_01")
    assert posts[0]["sma_status"] == "completed"
    assert posts[0]["post_url"] == "https://xhs.example/abc"
    assert "42" in posts[0]["last_metrics_json"]
    assert posts[0]["last_metrics_at"] is not None


def test_social_posts_filter_and_limit(tmp_path):
    store = _fresh_store(tmp_path)

    for i in range(3):
        store.record_social_post(sma_task_id=f"a-{i}", account_id="A")
    store.record_social_post(sma_task_id="b-0", account_id="B")

    assert len(store.list_social_posts()) == 4
    assert len(store.list_social_posts(account_id="A")) == 3
    assert len(store.list_social_posts(account_id="B")) == 1
    assert len(store.list_social_posts(limit=2)) == 2


def test_llm_call_record_and_summary(tmp_path):
    store = _fresh_store(tmp_path)

    store.record_llm_call(
        request_id="req-001",
        model="gpt-5.3-chat",
        symbol="600519",
        stage="market",
        provider="azure",
        prompt_tokens=1200,
        completion_tokens=800,
        cost_usd=0.015,
        latency_ms=4200,
    )
    store.record_llm_call(
        request_id="req-001",
        model="gpt-5.3-chat",
        symbol="600519",
        stage="trader",
        prompt_tokens=500,
        completion_tokens=300,
        cost_usd=0.006,
        latency_ms=1800,
    )

    summary = store.llm_cost_summary()
    assert summary["total_calls"] == 2
    assert summary["total_tokens"] == 2800
    assert round(summary["total_cost_usd"], 4) == 0.021


def test_llm_call_failure_flag(tmp_path):
    store = _fresh_store(tmp_path)

    store.record_llm_call(
        request_id="req-err",
        model="gpt-5.3-chat",
        success=False,
        error_msg="rate limit",
    )

    summary = store.llm_cost_summary()
    assert summary["total_calls"] == 1
    assert summary["total_cost_usd"] == 0.0
