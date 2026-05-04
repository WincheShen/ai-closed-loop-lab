#!/usr/bin/env python3
"""独立运行脚本 — 执行完整的 AI 闭环实验室流程。

Usage:
    # 日常交易闭环（模拟盘）
    python scripts/run_full_loop.py --mode mock

    # 日常交易闭环（只扫描不执行）
    python scripts/run_full_loop.py --mode scan

    # 周末复盘与 Prompt 进化
    python scripts/run_full_loop.py --mode feedback

    # 实盘（⚠️ 确认 TRADING_MODE=live 且已充分测试）
    python scripts/run_full_loop.py --mode live
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.graph.workflow import run_daily_pipeline, run_weekly_feedback
from src.infra.logger import setup_logging


async def main() -> None:
    parser = argparse.ArgumentParser(description="AI Closed Loop Lab — Full Pipeline")
    parser.add_argument(
        "--mode",
        choices=["scan", "mock", "paper", "live", "feedback"],
        default="mock",
        help="运行模式：scan=仅扫描, mock=模拟盘, paper=模拟盘, live=实盘, feedback=周末复盘",
    )
    args = parser.parse_args()

    setup_logging()

    if args.mode == "feedback":
        await run_weekly_feedback()
    else:
        await run_daily_pipeline(args.mode)


if __name__ == "__main__":
    asyncio.run(main())
