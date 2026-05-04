"""Phase 2 独立 smoke test，不依赖 pytest。

用法：
    python scripts/smoke_test_phase2.py         # 默认 60s timeout

特点：
- 每个测试单独计时，挂住会超时自愈
- 不发网络请求、不启动服务器、纯内存逻辑
- 10 秒内应该全部跑完；若超时请把 stderr 贴我看
"""
from __future__ import annotations

import signal
import sys
import time
from datetime import date, datetime
from pathlib import Path

# 让 import 走本地 src
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

TIMEOUT_PER_TEST = 10  # 秒


def _timeout_handler(signum, frame):
    raise TimeoutError(f"test exceeded {TIMEOUT_PER_TEST}s")


def run_test(name, func):
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TIMEOUT_PER_TEST)
    t0 = time.time()
    try:
        func()
    except Exception as e:
        signal.alarm(0)
        print(f"  ✗ {name}  ({time.time() - t0:.3f}s)  → {type(e).__name__}: {e}")
        return False
    signal.alarm(0)
    print(f"  ✓ {name}  ({time.time() - t0:.3f}s)")
    return True


# ---------------------------------------------------------------------------

def test_imports():
    from social_media_dispatcher import TopicRouter, SmaClient  # noqa: F401
    from social_media_dispatcher.schemas import TopicPayload  # noqa: F401


def test_masking():
    from social_media_dispatcher.topic_router import _mask_symbol, _mask_name
    assert _mask_symbol("600519") == "60xxxx"
    assert _mask_symbol("000858") == "00xxxx"
    assert _mask_name("贵州茅台") == "贵X台"
    assert _mask_name("中") == "中X"
    assert _mask_name("") == "某股"


def test_from_daily_picks_full():
    from social_media_dispatcher import TopicRouter
    from stock_analyzer.pipelines.daily_scan import DailyPicks, RecommendedStock

    r = RecommendedStock(
        symbol="600519", name="贵州茅台", price=1500.0, change_pct=4.2,
        industry="白酒", rule_score=5.0, matched_rules=["not_st"],
    )
    r.bucket = "aggressive"
    r.reasoning = "规则5.0 + Agent BUY"

    r2 = RecommendedStock(
        symbol="000858", name="五粮液", price=180.0, change_pct=2.1,
        industry="白酒", rule_score=4.0, matched_rules=["not_st"],
    )
    r2.bucket = "stable"
    r2.reasoning = "稳健 Agent HOLD"

    picks = DailyPicks(
        pick_date=date(2026, 4, 26),
        is_mock_data=True,
        hot_sectors=["白酒", "AI算力", "低空经济"],
        aggressive=[r], stable=[r2],
    )
    payload = TopicRouter().from_daily_picks(picks, account_id="XHS_01")

    assert payload.account_id == "XHS_01"
    assert payload.kind == "daily_picks"
    assert "白酒" in payload.description
    assert len(payload.context.recommendations) == 2

    # 关键：脱敏不泄漏
    js = payload.model_dump_json()
    for leak in ("600519", "000858", "贵州茅台", "五粮液"):
        assert leak not in js, f"泄漏: {leak}"
    assert "60xxxx" in js
    assert "00xxxx" in js


def test_from_daily_picks_empty():
    from social_media_dispatcher import TopicRouter
    from stock_analyzer.pipelines.daily_scan import DailyPicks

    picks = DailyPicks(pick_date=date(2026, 4, 26), is_mock_data=True, hot_sectors=[])
    p = TopicRouter().from_daily_picks(picks, account_id="XHS_01")
    assert "暂无明显主线" in p.description


def test_from_trade_record():
    from social_media_dispatcher import TopicRouter

    p = TopicRouter().from_trade_record(
        record_id="abc123",
        safe_text="今天上车白酒龙头，关注20元附近",
        received_at=datetime(2026, 4, 26, 10, 30),
        account_id="XHS_01",
    )
    assert p.kind == "trade_record"
    assert p.context.trade_record is not None
    assert p.context.trade_record.record_id == "abc123"


def test_from_manual():
    from social_media_dispatcher import TopicRouter
    p = TopicRouter().from_manual("聊聊低空经济中线机会", account_id="XHS_02")
    assert p.kind == "manual"
    assert p.account_id == "XHS_02"
    assert "低空经济" in p.description


def test_client_offline():
    """SmaClient 在目标服务不可达时应优雅降级，不抛异常。"""
    from social_media_dispatcher import SmaClient
    from social_media_dispatcher.schemas import DispatchResult

    client = SmaClient(base_url="http://127.0.0.1:1",  # 故意打到一个无效端口
                       timeout=1.0)
    from social_media_dispatcher import TopicRouter
    payload = TopicRouter().from_manual("test", account_id="XHS_01")
    result = client.dispatch(payload)
    assert isinstance(result, DispatchResult)
    assert result.success is False
    assert result.error is not None


# ---------------------------------------------------------------------------

TESTS = [
    ("imports", test_imports),
    ("masking", test_masking),
    ("from_daily_picks_full (leak-free)", test_from_daily_picks_full),
    ("from_daily_picks_empty", test_from_daily_picks_empty),
    ("from_trade_record", test_from_trade_record),
    ("from_manual", test_from_manual),
    ("client_offline (graceful)", test_client_offline),
]


def main():
    print("Phase 2 smoke tests")
    print("=" * 50)
    results = [run_test(n, f) for n, f in TESTS]
    print("=" * 50)
    ok, total = sum(results), len(results)
    print(f"{ok}/{total} passed")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
