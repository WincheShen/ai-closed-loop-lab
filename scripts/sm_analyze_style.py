"""CLI: 对交易员的 inferred trades 做风格分析 (M6)。

用法：
    python scripts/sm_analyze_style.py --trader 二池 [--philosophy-file path.txt]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # so `src` package is importable
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from strategy_mining.holdings.style_analyzer import analyze  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trader", required=True)
    p.add_argument("--philosophy-file", type=Path,
                    help="交易员自述哲学文本文件（utf-8）")
    p.add_argument("--output", type=Path, default=None,
                    help="JSON 输出路径，默认打印到 stdout")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    phil = None
    if args.philosophy_file:
        phil = args.philosophy_file.read_text(encoding="utf-8")

    report = analyze(args.trader, philosophy_override=phil)

    out = {
        "trader_alias": report.trader_alias,
        "style_tags": report.style_tags,
        "avg_holding_days": report.avg_holding_days,
        "win_rate": report.win_rate,
        "profit_factor": report.profit_factor,
        "max_single_loss_pct": report.max_single_loss_pct,
        "max_single_win_pct": report.max_single_win_pct,
        "sector_tilt": report.sector_tilt,
        "position_sizing_pattern": report.position_sizing_pattern,
        "risk_management_notes": report.risk_management_notes,
        "alignment_with_philosophy": report.alignment_with_philosophy,
    }
    payload = json.dumps(out, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        # 默认保存到 reports/ 目录
        reports_dir = ROOT / "reports"
        reports_dir.mkdir(exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = reports_dir / f"{args.trader}_{date_str}.json"
        out_path.write_text(payload, encoding="utf-8")
        print(f"Report written to {out_path}")
        print("---")
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
