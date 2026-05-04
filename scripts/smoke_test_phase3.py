"""Phase 3 smoke test — TradingAgents adapter mapping logic.

不发真 LLM 请求，只测：
1. decision normalize 容错
2. final_state → Report 字段映射
3. 现价/基本面字段补全（meta 注入）
4. trend 启发式归类
5. truncate 长度约束

用法：
    python scripts/smoke_test_phase3.py
"""
from __future__ import annotations

import signal
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

TIMEOUT = 5


def _h(s, f):
    raise TimeoutError("test exceeded %ds" % TIMEOUT)


def run(name, fn):
    signal.signal(signal.SIGALRM, _h)
    signal.alarm(TIMEOUT)
    t0 = time.time()
    try:
        fn()
    except Exception as e:
        signal.alarm(0)
        print(f"  ✗ {name}  ({time.time() - t0:.3f}s)  → {type(e).__name__}: {e}")
        return False
    signal.alarm(0)
    print(f"  ✓ {name}  ({time.time() - t0:.3f}s)")
    return True


# ---------------------------------------------------------------------------

def test_normalize_decision_basic():
    from trading_agent_service.analysis.tradingagents_adapter import _normalize_decision
    assert _normalize_decision("BUY") == ("BUY", 0.70)
    assert _normalize_decision("SELL") == ("SELL", 0.70)
    assert _normalize_decision("HOLD") == ("HOLD", 0.50)
    assert _normalize_decision("OVERWEIGHT") == ("BUY", 0.62)
    assert _normalize_decision("UNDERWEIGHT") == ("SELL", 0.62)


def test_normalize_decision_noisy():
    from trading_agent_service.analysis.tradingagents_adapter import _normalize_decision
    # LLM 偶尔会输出带解释的串
    assert _normalize_decision("BUY.") == ("BUY", 0.70)
    assert _normalize_decision("Decision: BUY")[0] == "BUY"
    assert _normalize_decision("My answer is hold")[0] == "HOLD"
    assert _normalize_decision("")[0] == "HOLD"  # fallback
    assert _normalize_decision("garbage text")[0] == "HOLD"


def test_truncate():
    from trading_agent_service.analysis.tradingagents_adapter import _truncate
    assert _truncate("abc", 10) == "abc"
    assert _truncate("a" * 100, 10) == "aaaaaaaaaa…"
    assert _truncate("", 10) == ""
    assert _truncate(None, 10) == ""  # type: ignore


def test_extract_trend():
    from trading_agent_service.analysis.tradingagents_adapter import RealTradingAgentsAdapter
    f = RealTradingAgentsAdapter._extract_trend
    assert f("整体上行趋势明显，多次突破上轨") == "上行"
    assert f("持续下跌，多次破位") == "下行"
    assert f("窄幅震荡，无明确方向") == "震荡"
    assert f("") == "未知"


def test_to_report_full_mapping():
    """核心：mock 一个 final_state，验证字段映射。"""
    from trading_agent_service.analysis.tradingagents_adapter import RealTradingAgentsAdapter

    adapter = RealTradingAgentsAdapter.__new__(RealTradingAgentsAdapter)
    adapter.available = True

    meta = {
        "name": "贵州茅台",
        "current_price": 1500.0,
        "industry": "白酒",
        "market_cap_yi": 18000.0,
        "pe_ttm": 25.5,
        "pb": 8.2,
        "roe": 30.0,
    }
    final_state = {
        "investment_debate_state": {
            "bull_history": "多方观点：业绩稳健，护城河深，估值合理。继续看好。",
            "bear_history": "空方观点：消费疲软，限价令风险。短期承压。",
            "judge_decision": "judge: 多方占优",
        },
        "market_report": "技术面：MA5/MA10 多头排列，整体上行趋势，关键支撑1450。",
        "fundamentals_report": "基本面优秀，ROE 持续 30%+",
        "final_trade_decision": "综合判断：建议买入。理由：基本面稳健 + 技术面突破。",
    }

    report = adapter._to_report(
        symbol="600519",
        depth="deep",
        meta=meta,
        final_state=final_state,
        decision_raw="BUY",
    )

    assert report.symbol == "600519"
    assert report.name == "贵州茅台"
    assert report.current_price == 1500.0
    assert report.final_decision == "BUY"
    assert report.confidence == 0.70

    # 报价区间 ±5%
    assert report.reevaluation_price_range == (1425.0, 1575.0)

    # 基本面数值字段直通
    assert report.fundamental.industry == "白酒"
    assert report.fundamental.pe_ttm == 25.5
    assert report.fundamental.pb == 8.2
    assert report.fundamental.market_cap_yi == 18000.0

    # 多空观点来自辩论历史
    assert "业绩稳健" in report.bull_case
    assert "消费疲软" in report.bear_case

    # 趋势从 market_report 启发式提取
    assert report.technical.trend == "上行"
    assert "MA5" in report.technical.summary

    # valid_until 为 today + 3
    assert (report.valid_until - date.today()).days == 3


def test_to_report_with_missing_data():
    """容错：final_state 字段缺失 / 价格未知。"""
    from trading_agent_service.analysis.tradingagents_adapter import RealTradingAgentsAdapter

    adapter = RealTradingAgentsAdapter.__new__(RealTradingAgentsAdapter)
    adapter.available = True

    meta = {
        "name": "代码999999",
        "current_price": 0.0,
        "industry": "",
        "market_cap_yi": None,
        "pe_ttm": None,
        "pb": None,
        "roe": None,
    }
    final_state = {}  # 全空

    report = adapter._to_report(
        symbol="999999",
        depth="quick",
        meta=meta,
        final_state=final_state,
        decision_raw="HOLD",
    )

    assert report.final_decision == "HOLD"
    assert report.confidence == 0.50
    assert report.bull_case == "（多方观点缺失）"
    assert report.bear_case == "（空方观点缺失）"
    assert report.technical.trend == "未知"
    # quick depth → valid_until = today + 1
    assert (report.valid_until - date.today()).days == 1


def test_factory_mock():
    """factory: prefer='mock' 必返回 MockAnalyzer。"""
    from trading_agent_service.analysis import get_analyzer
    from trading_agent_service.analysis.adapter import MockAnalyzer
    a = get_analyzer("mock")
    assert isinstance(a, MockAnalyzer)


def test_factory_tradingagents_unavailable():
    """factory: prefer='tradingagents' 在未装时应抛 RuntimeError，不静默 fallback。"""
    from trading_agent_service.analysis import get_analyzer

    try:
        import tradingagents  # noqa: F401
        # 真装了，跳过这个测试
        print("    (tradingagents 已装，skip 不可用分支测试)")
        return
    except ImportError:
        pass

    try:
        get_analyzer("tradingagents")
        raise AssertionError("应抛 RuntimeError")
    except RuntimeError as e:
        assert "tradingagents 不可用" in str(e)


# ---------------------------------------------------------------------------

TESTS = [
    ("normalize_decision_basic", test_normalize_decision_basic),
    ("normalize_decision_noisy", test_normalize_decision_noisy),
    ("truncate", test_truncate),
    ("extract_trend (heuristic)", test_extract_trend),
    ("_to_report full mapping", test_to_report_full_mapping),
    ("_to_report with missing data (graceful)", test_to_report_with_missing_data),
    ("factory: mock", test_factory_mock),
    ("factory: tradingagents unavailable", test_factory_tradingagents_unavailable),
]


def main():
    print("Phase 3 smoke tests (TradingAgents adapter mapping)")
    print("=" * 50)
    results = [run(n, f) for n, f in TESTS]
    print("=" * 50)
    ok, total = sum(results), len(results)
    print(f"{ok}/{total} passed")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
