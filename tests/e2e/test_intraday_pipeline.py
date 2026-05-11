#!/usr/bin/env python3
"""集成测试 — 模拟完整一天的交易 + 复审 + 收盘发帖流程。

无需真实行情/LLM API，全部用 mock 数据跑通:
1. 建仓: 模拟2只持仓 (含 thesis)
2. 盘中复审: 模拟一轮 intraday_review (force=True 跳过交易时间)
3. 收盘分析: 生成发帖内容 (dry_run=True 不实际发布)
4. 验证: 所有数据正确持久化到 Central Brain
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    # Use a temp DB so we don't pollute real data
    tmp_db = tempfile.mktemp(suffix=".db")
    os.environ["DB_PATH"] = tmp_db
    os.environ["TRADING_MODE"] = "mock"

    print("=" * 60)
    print("集成测试: 模拟完整一天的交易+发帖流程")
    print(f"临时数据库: {tmp_db}")
    print("=" * 60)

    try:
        _run_all_steps()
        print("\n" + "=" * 60)
        print("✅ 集成测试全部通过!")
        print("=" * 60)
    finally:
        if os.path.exists(tmp_db):
            os.unlink(tmp_db)

    return


def _run_all_steps() -> None:
    # Force re-init singleton with new DB path
    from src.central_brain.metadata_store import CentralBrain, MemoryStore
    CentralBrain._instance = None

    from src.central_brain import get_central_brain

    brain = get_central_brain()
    today = date.today().isoformat()

    # ------------------------------------------------------------------
    # Step 1: 模拟建仓 — 2只持仓，带完整 thesis
    # ------------------------------------------------------------------
    print("\n--- Step 1: 模拟建仓 ---")

    brain.store.open_position(
        position_id="POS-TEST-001",
        symbol="600519",
        name="贵州茅台",
        entry_price=1850.00,
        qty=100,
        entry_date=today,
        thesis="白酒板块回调企稳，茅台PE回到25x合理区间。消费数据环比改善，北向资金净流入。",
        strategy="价值回归+板块轮动",
        bull_case="消费复苏+外资回流，半年目标2100",
        bear_case="经济下行+三公消费限制",
        target_price=2100.0,
        stop_loss=1750.0,
    )
    brain.store.open_position(
        position_id="POS-TEST-002",
        symbol="002371",
        name="北方华创",
        entry_price=320.00,
        qty=200,
        entry_date=today,
        thesis="半导体设备国产替代加速，公司订单饱满，新产线量产在即。",
        strategy="热点+基本面共振",
        bull_case="半导体周期回暖+国产替代订单，目标380",
        bear_case="出口管制升级+客户延迟交付",
        target_price=380.0,
        stop_loss=295.0,
    )

    positions = brain.store.list_open_positions()
    print(f"  建仓完成: {len(positions)} 只")
    for p in positions:
        print(f"    {p['symbol']} {p['name']} @ {p['entry_price']}")
    assert len(positions) == 2, f"Expected 2 positions, got {len(positions)}"

    # ------------------------------------------------------------------
    # Step 2: 盘中复审 — mock LLM response
    # ------------------------------------------------------------------
    print("\n--- Step 2: 盘中复审 (mock LLM) ---")

    mock_review_responses = iter([
        # First position: HOLD
        json.dumps({
            "action": "HOLD",
            "confidence": 0.8,
            "reason": "白酒板块今日横盘整理，thesis未变，量能正常",
            "thesis_status": "intact",
            "key_observation": "盘中窄幅震荡，成交量略低于5日均量",
            "risk_flag": "",
        }),
        # Second position: REDUCE
        json.dumps({
            "action": "REDUCE",
            "confidence": 0.7,
            "reason": "半导体板块午后冲高回落，量价背离，先兑现部分利润",
            "thesis_status": "weakened",
            "key_observation": "午后放量冲高但未站稳前高，量价背离明显",
            "risk_flag": "量价背离+板块轮动迹象",
        }),
    ])

    def mock_llm_invoke(messages):
        resp = MagicMock()
        resp.content = next(mock_review_responses)
        resp.usage_metadata = {"total_tokens": 500}
        return resp

    mock_llm = MagicMock()
    mock_llm.invoke = mock_llm_invoke

    with patch("src.agents.reviewer.position_reviewer.get_llm", return_value=mock_llm):
        from src.agents.reviewer.intraday_loop import run_intraday_review
        results = asyncio.run(run_intraday_review(force=True))

    print(f"  复审完成: {len(results)} 只持仓")
    for r in results:
        print(f"    {r.get('symbol', '?')}: {r['action']} — {r.get('reason', '')[:40]}")
        if r.get("executed"):
            print(f"      执行: {r.get('trade_side')} {r.get('trade_qty')} 股 @ {r.get('trade_price', '?')}")

    assert len(results) == 2, f"Expected 2 reviews, got {len(results)}"
    assert results[0]["action"] == "HOLD"
    assert results[1]["action"] == "REDUCE"

    # Verify DB state
    pos2 = brain.store.get_position("POS-TEST-002")
    print(f"\n  验证: 北方华创 持仓量 {pos2['current_qty']}（减仓后）")
    assert pos2["last_review_action"] == "REDUCE"

    reviews_001 = brain.store.list_position_reviews("POS-TEST-001")
    reviews_002 = brain.store.list_position_reviews("POS-TEST-002")
    print(f"  验证: 茅台复审记录 {len(reviews_001)} 条, 华创复审记录 {len(reviews_002)} 条")
    assert len(reviews_001) >= 1
    assert len(reviews_002) >= 1

    # ------------------------------------------------------------------
    # Step 3: 收盘分析 — mock LLM response
    # ------------------------------------------------------------------
    print("\n--- Step 3: 收盘分析 (mock LLM) ---")

    mock_closing_response = json.dumps({
        "title": "AI交易日记 | 半导体冲高回落，白酒纹丝不动",
        "content": (
            "今天AI模型监控了2只持仓，产生了1个交易信号。\n\n"
            "🍷 白酒板块横盘整理，AI判断thesis不变继续持有。量能偏弱，"
            "但北向资金今日净流入，不构成卖出理由。\n\n"
            "💾 半导体午后冲高回落，AI识别出量价背离信号，果断减仓兑现部分利润。"
            "虽然长期逻辑（国产替代）未变，但短期获利盘压力明显。\n\n"
            "📊 今日操作: 1次减仓\n"
            "📈 整体表现: 持仓中2只（1只减仓）\n\n"
            "明天关注半导体能否企稳回升，白酒是否有补涨机会。\n\n"
            "⚠️ 此为AI实验记录，不构成投资建议。\n"
            "#AI交易 #量化投资 #沈经理实盘"
        ),
        "highlights": ["半导体量价背离触发减仓", "白酒thesis稳固继续持有"],
        "mood": "neutral",
    })

    mock_closing_llm = MagicMock()
    mock_closing_resp = MagicMock()
    mock_closing_resp.content = mock_closing_response
    mock_closing_llm.invoke = MagicMock(return_value=mock_closing_resp)

    with patch("src.agents.reviewer.closing_analysis.get_llm", return_value=mock_closing_llm):
        from src.agents.reviewer.closing_analysis import run_closing_analysis
        closing_result = asyncio.run(run_closing_analysis(dry_run=True))

    post = closing_result.get("post")
    print(f"  标题: {post.get('title', 'N/A')}")
    print(f"  内容长度: {len(post.get('content', ''))} 字")
    print(f"  亮点: {post.get('highlights', [])}")
    print(f"  情绪: {post.get('mood', '?')}")
    assert post is not None
    assert "title" in post
    assert len(post.get("content", "")) > 50

    # ------------------------------------------------------------------
    # Step 4: 验证数据完整性
    # ------------------------------------------------------------------
    print("\n--- Step 4: 数据完整性验证 ---")

    # Positions
    all_pos = brain.store.list_open_positions()
    print(f"  持仓记录: {len(all_pos)} 只 open")

    # Events
    events = brain.store.query_events(limit=20)
    print(f"  事件记录: {len(events)} 条")
    event_types = set(e.get("event_type", "") for e in events)
    print(f"  事件类型: {event_types}")
    assert "intraday_review_complete" in event_types
    assert "closing_analysis_generated" in event_types

    # Reviews
    total_reviews = 0
    for p in all_pos:
        reviews = brain.store.list_position_reviews(p["position_id"])
        total_reviews += len(reviews)
    print(f"  复审记录: {total_reviews} 条")

    print("\n  所有验证通过 ✓")


if __name__ == "__main__":
    main()
