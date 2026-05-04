"""CLI: 列出某公众号的历史文章 URL，并写入 strategy_mining.wechat_articles。

使用前请确认 .env 已配置：
    PG_*               —— PostgreSQL 连接
    WX_MP_TOKEN        —— 个人订阅号 token
    WX_MP_COOKIE       —— 个人订阅号登录 cookie

示例：
    python scripts/sm_list_wechat_urls.py --nickname "某某公众号" --max 200
    python scripts/sm_list_wechat_urls.py --nickname "某某" --biz MzU... --max 50 --sleep 5
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 让 src/ 可被导入
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from strategy_mining.article_repo import upsert_url_listing  # noqa: E402
from strategy_mining.db import ping  # noqa: E402
from strategy_mining.wechat_url_lister import list_articles  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nickname", required=True, help="公众号显示名（精确）")
    parser.add_argument("--biz", default="", help="公众号 __biz 参数（可选，加速匹配）")
    parser.add_argument("--max", dest="max_count", type=int, default=50)
    parser.add_argument("--page-size", type=int, default=5)
    parser.add_argument("--sleep", type=float, default=3.0,
                        help="每页之间睡眠秒数；建议 >= 2")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印结果，不写库")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    info = ping()
    print(f"DB OK: {info['database']} as {info['user']}, "
          f"strategy_mining tables={info['strategy_mining_tables']}")

    rows = list_articles(
        nickname=args.nickname,
        biz=args.biz,
        max_count=args.max_count,
        page_size=args.page_size,
        sleep_seconds=args.sleep,
    )
    print(f"\nfetched {len(rows)} article entries")
    for r in rows[:5]:
        print(f"  - {r.get('title')[:60]}  {r.get('link')}")
    if len(rows) > 5:
        print(f"  ... (+{len(rows)-5} more)")

    if args.dry_run:
        print("\n[dry-run] not writing to DB.")
        return 0

    inserted = upsert_url_listing(rows, account_name=args.nickname, biz=args.biz)
    print(f"\nupserted {inserted} rows into strategy_mining.wechat_articles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
