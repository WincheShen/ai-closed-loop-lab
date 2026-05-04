#!/usr/bin/env python3
"""独立运行脚本 — 仅执行探索者扫描，查看候选票列表。

Usage:
    python scripts/run_explorer.py --mode scan
    python scripts/run_explorer.py --mode mock   # 扫描后走完整闭环
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 确保能 import src
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.graph.workflow import run_daily_pipeline
from src.infra.config import cfg
from src.infra.logger import setup_logging


async def main() -> None:
    parser = argparse.ArgumentParser(description="Explorer Scanner")
    parser.add_argument("--mode", choices=["scan", "mock", "paper"], default="scan")
    args = parser.parse_args()

    setup_logging()

    if args.mode == "scan":
        # 只运行 Explorer 节点
        from src.agents.explorer.scanner import ExplorerScanner
        from src.graph.state import create_empty_state
        import uuid
        from datetime import datetime

        session_id = f"explorer-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        scanner = ExplorerScanner(session_id)
        hot = scanner.fetch_hot_sectors()
        candidates = scanner.scan_market()
        validated = scanner.cross_validate_with_sentiment(candidates, hot)

        print(f"\n🔥 热点板块 ({len(hot)}个):")
        for h in hot:
            print(f"  • {h}")

        print(f"\n📊 候选票 (Top 50 → 交叉验证后 {len(validated)}只):")
        for i, c in enumerate(validated[:20], 1):
            print(
                f"  {i:2d}. {c['symbol']} {c['name']:8s} | "
                f"板块: {c['sector']:8s} | "
                f"Qlib: {c['qlib_score']:.3f} | "
                f"{c['hot_reason'][0][:30]}..."
            )
    else:
        # 走完整闭环
        await run_daily_pipeline(args.mode)


if __name__ == "__main__":
    asyncio.run(main())
