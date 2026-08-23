"""python -m src.strategy_lab --help

最小 CLI：对本地已有日线缓存跑检测器（本轮不拉全市场）。
"""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="strategy_lab 最小回测")
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--start", default="2024-01-01")
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "ok": False,
                "reason": "需要日线缓存后才能跑全市场回测。单测用固定 fixture。",
                "strategy": args.strategy,
                "start": args.start,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
