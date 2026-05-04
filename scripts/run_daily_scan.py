"""运行每日选股流水线（一次性）。

用法：
    # TradingAgent 服务已起：
    python scripts/run_daily_scan.py

    # 不调用 TradingAgent（只跑规则引擎）：
    python scripts/run_daily_scan.py --no-agent
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
# 同时加项目根目录（使 ``from src.xxx`` 这类旧导入可用）和 src/（新模块约定）
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from stock_analyzer.pipelines import DailyScanPipeline  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-agent", action="store_true",
                        help="跳过 TradingAgent 调用，只跑规则引擎")
    parser.add_argument("--agent-url", default="http://localhost:8001")
    parser.add_argument("--max-agent-calls", type=int, default=8)
    # Phase 2: SMA dispatch
    parser.add_argument("--sma-account",
                        help="SMA 账号 ID（如 XHS_01），设置后自动推送选股结果")
    parser.add_argument("--sma-url", default="http://localhost:8003",
                        help="Social-media-automation 服务地址")
    args = parser.parse_args()

    pipeline = DailyScanPipeline(
        trading_agent_url=None if args.no_agent else args.agent_url,
        max_agent_calls=args.max_agent_calls,
        sma_base_url=args.sma_url if args.sma_account else None,
        sma_account_id=args.sma_account,
    )
    picks = pipeline.run()

    print("\n" + "=" * 60)
    print(f"📅 选股日期: {picks.pick_date}  (mock={picks.is_mock_data})")
    print(f"🔥 热门板块: {', '.join(picks.hot_sectors)}")
    print("=" * 60)
    print(f"\n🚀 激进推荐 ({len(picks.aggressive)} 只):")
    for s in picks.aggressive:
        print(f"  • {s.symbol} {s.name}  价 {s.price}  +{s.change_pct}%  "
              f"得分 {s.rule_score:.1f}  {s.reasoning}")
    print(f"\n🛡  稳健推荐 ({len(picks.stable)} 只):")
    for s in picks.stable:
        print(f"  • {s.symbol} {s.name}  价 {s.price}  +{s.change_pct}%  "
              f"得分 {s.rule_score:.1f}  {s.reasoning}")
    print(f"\n📊 候选池: {len(picks.candidates)} 只 (前 5)")
    for s in picks.candidates[:5]:
        print(f"  • {s.symbol} {s.name}  得分 {s.rule_score:.1f}  "
              f"命中 {','.join(s.matched_rules)}")


if __name__ == "__main__":
    main()
