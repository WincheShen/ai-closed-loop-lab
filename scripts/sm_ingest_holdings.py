"""CLI: 批量抽取持仓截图 → VLM → 解析 → 入库。

用法：
    python scripts/sm_ingest_holdings.py \
        --trader 二池 \
        --dir /Users/neo/Documents/Stock_investment/二池

    python scripts/sm_ingest_holdings.py \
        --trader 二池 --file /path/to/2026-04-30.jpg   # 单张

依赖 .env:
    OPENAI_API_KEY / OPENAI_BASE_URL   VLM 调用
    PG_*                                PostgreSQL
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from strategy_mining.holdings.pipeline import ingest_one  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trader", required=True, help="交易员别名，例如 '二池'")
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--dir", help="图片目录")
    grp.add_argument("--file", help="单张图片")
    p.add_argument("--pattern", default="*.jpg,*.png,*.jpeg",
                   help="glob 模式，逗号分隔，默认 *.jpg,*.png,*.jpeg")
    p.add_argument("--limit", type=int, default=None,
                   help="限制处理张数（测试用）")
    p.add_argument("--skip-existing", action="store_true",
                   help="跳过已入库的 trade_date（按文件名推断）")
    p.add_argument("--vlm-model", default=None, help="覆盖 HOLDINGS_VLM_MODEL")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.file:
        files = [Path(args.file)]
    else:
        d = Path(args.dir)
        files = []
        for pat in args.pattern.split(","):
            files.extend(d.glob(pat.strip()))
        files = sorted({f for f in files if f.is_file()})

    if args.limit:
        files = files[: args.limit]

    if args.skip_existing:
        from strategy_mining.holdings.snapshot_repo import list_snapshots
        done = {s["trade_date"].isoformat() for s in list_snapshots(
            args.trader, page_type=None,
        )}
        files = [f for f in files if f.stem not in done]
        print(f"skipping {len(done)} existing date(s); {len(files)} remaining")

    print(f"Processing {len(files)} file(s) for trader={args.trader!r}")
    print("-" * 78)

    success = 0
    fail: list[tuple[str, str]] = []
    t_total = time.perf_counter()
    for i, f in enumerate(files, 1):
        t0 = time.perf_counter()
        try:
            info = ingest_one(f, trader_alias=args.trader,
                              vlm_model=args.vlm_model)
            dt = time.perf_counter() - t0
            flag = "⚠️" if info["needs_review"] else "✅"
            print(f"  [{i:2d}/{len(files)}] {flag} {info['image']:18s}  "
                  f"{info['page_type']:17s}  {info['broker'] or '-':4s} "
                  f"{info['account_suffix'] or '-':5s}  "
                  f"holdings={info['active']}/{info['holdings']}  "
                  f"id={info['snapshot_id']:>3}  ({dt:.1f}s)")
            success += 1
        except Exception as exc:  # noqa: BLE001
            dt = time.perf_counter() - t0
            print(f"  [{i:2d}/{len(files)}] ❌ {f.name}  FAILED in {dt:.1f}s: {exc!r}")
            fail.append((f.name, repr(exc)))

    print("-" * 78)
    print(f"Done in {time.perf_counter() - t_total:.1f}s: {success} ok, {len(fail)} failed")
    for name, err in fail:
        print(f"  - {name}: {err}")
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
