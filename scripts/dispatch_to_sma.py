"""手动把选题推送到 Social-media-automation。

用法：
    # 1. 用最近的 daily_picks 推一个 topic
    python scripts/dispatch_to_sma.py from-picks --account XHS_01

    # 2. 用指定日期的 daily_picks
    python scripts/dispatch_to_sma.py from-picks --account XHS_01 --date 2026-04-26

    # 3. 人工选题（FR-2.5 二次加工模式）
    python scripts/dispatch_to_sma.py manual --account XHS_01 \
        --text "今天聊聊低空经济板块的中线机会"

    # 4. 健康检查
    python scripts/dispatch_to_sma.py health
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from social_media_dispatcher import SmaClient, TopicRouter  # noqa: E402
from stock_analyzer.pipelines.daily_scan import (                # noqa: E402
    DailyPicks,
    RecommendedStock,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _load_picks(picks_path: Path) -> DailyPicks:
    raw = json.loads(picks_path.read_text(encoding="utf-8"))

    def _to_rec(d: dict) -> RecommendedStock:
        return RecommendedStock(**d)

    return DailyPicks(
        pick_date=date.fromisoformat(raw["pick_date"]),
        is_mock_data=raw.get("is_mock_data", False),
        hot_sectors=raw.get("hot_sectors", []),
        aggressive=[_to_rec(d) for d in raw.get("aggressive", [])],
        stable=[_to_rec(d) for d in raw.get("stable", [])],
        candidates=[_to_rec(d) for d in raw.get("candidates", [])],
    )


def _find_latest_picks(picks_dir: Path) -> Path | None:
    files = sorted(picks_dir.glob("*.json"))
    return files[-1] if files else None


def cmd_from_picks(args) -> int:
    picks_dir = Path(args.picks_dir)
    if args.date:
        picks_path = picks_dir / f"{args.date}.json"
    else:
        latest = _find_latest_picks(picks_dir)
        if latest is None:
            print(f"no daily_picks files in {picks_dir}", file=sys.stderr)
            return 2
        picks_path = latest

    if not picks_path.exists():
        print(f"picks not found: {picks_path}", file=sys.stderr)
        return 2

    picks = _load_picks(picks_path)
    payload = TopicRouter().from_daily_picks(picks, account_id=args.account)
    print(f"📤 dispatching topic from {picks_path.name} → {args.account}")
    print(f"   description: {payload.description[:100]}...")

    client = SmaClient(base_url=args.url)
    result = client.dispatch(payload)
    print(f"   result: success={result.success} task_id={result.sma_task_id} "
          f"status={result.sma_status} error={result.error}")
    return 0 if result.success else 1


def cmd_manual(args) -> int:
    payload = TopicRouter().from_manual(args.text, account_id=args.account)
    print(f"📤 manual topic → {args.account}: {args.text[:80]}...")
    client = SmaClient(base_url=args.url)
    result = client.dispatch(payload)
    print(f"   result: success={result.success} task_id={result.sma_task_id}")
    return 0 if result.success else 1


def cmd_health(args) -> int:
    client = SmaClient(base_url=args.url)
    try:
        info = client.health()
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"❌ {e}", file=sys.stderr)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8003",
                        help="Social-media-automation base URL")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("from-picks", help="用 daily_picks 文件触发")
    p1.add_argument("--account", required=True, help="SMA 账号 ID 如 XHS_01")
    p1.add_argument("--date", help="YYYY-MM-DD，不传则取最新")
    p1.add_argument("--picks-dir", default="data/daily_picks")
    p1.set_defaults(func=cmd_from_picks)

    p2 = sub.add_parser("manual", help="人工选题")
    p2.add_argument("--account", required=True)
    p2.add_argument("--text", required=True)
    p2.set_defaults(func=cmd_manual)

    p3 = sub.add_parser("health", help="检查 SMA 服务可达性")
    p3.set_defaults(func=cmd_health)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
