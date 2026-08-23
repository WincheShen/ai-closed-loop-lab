"""运行每日管线（scan-only）。旧 DailyScanPipeline 不再作为默认入口。

用法：
    python scripts/run_daily_scan.py
    python scripts/run_daily_scan.py --mode mock
    python scripts/run_daily_scan.py --legacy   # 仅调试旧规则流水线
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="scan", choices=["scan", "mock", "paper"])
    parser.add_argument("--persona", default=None)
    parser.add_argument("--legacy", action="store_true", help="跑已废弃的 DailyScanPipeline")
    parser.add_argument("--no-agent", action="store_true")
    parser.add_argument("--agent-url", default=None)
    args = parser.parse_args()

    if args.legacy:
        from stock_analyzer.pipelines import DailyScanPipeline
        from src.infra.config import cfg
        url = None if args.no_agent else (args.agent_url or cfg().get("trading_agent_url"))
        picks = DailyScanPipeline(trading_agent_url=url).run()
        print(f"legacy scan {picks.pick_date} candidates={len(picks.candidates)}")
        return

    from src.graph.workflow import run_daily_pipeline
    result = asyncio.run(run_daily_pipeline(args.mode, persona_id=args.persona))
    print(
        f"session={result.get('session_id')} "
        f"signals={len(result.get('trade_signals') or [])} "
        f"candidates={len(result.get('target_stocks') or [])}"
    )


if __name__ == "__main__":
    main()
