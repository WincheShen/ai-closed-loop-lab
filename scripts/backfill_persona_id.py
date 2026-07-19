"""数据回填：将 persona_id IS NULL 的历史持仓/信号迁移到默认人格。

背景：早期版本入库的 positions/trade_signals 没有 persona_id 字段值。
上多人格调度后，`list_open_positions(persona_id=xxx)` 严格过滤会漏掉这些持仓，
导致它们永远不被盘中复审 → 出现"买了不卖"现象（典型案例：铜陵有色 POS-816CA4E5）。

用法（容器内）：
    python /app/scripts/backfill_persona_id.py --default-persona short_term_hot_rotation_v1
    python /app/scripts/backfill_persona_id.py --dry-run  # 仅预览

或者 host 直连 DB：
    python scripts/backfill_persona_id.py --db data/central_brain.db --default-persona short_term_hot_rotation_v1
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def _default_db_path() -> str:
    # 容器内约定
    if Path("/app/data/central_brain.db").exists():
        return "/app/data/central_brain.db"
    # 本地开发
    return str(Path(__file__).resolve().parents[1] / "data" / "central_brain.db")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=_default_db_path(), help="SQLite 路径")
    parser.add_argument(
        "--default-persona", default="short_term_hot_rotation_v1",
        help="要回填的默认人格 ID",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅打印，不执行更新")
    parser.add_argument(
        "--include-closed", action="store_true",
        help="同时回填已平仓的历史持仓（默认只处理 open）",
    )
    args = parser.parse_args()

    print(f"DB: {args.db}")
    print(f"默认人格: {args.default_persona}")
    print(f"dry-run: {args.dry_run}")
    print(f"包含 closed: {args.include_closed}")
    print("-" * 60)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    # 1. 预览 open 持仓
    open_rows = conn.execute(
        "SELECT position_id, symbol, name, entry_price, current_qty, entry_date, status "
        "FROM positions WHERE persona_id IS NULL AND status = 'open' "
        "ORDER BY entry_date"
    ).fetchall()
    print(f"open 持仓中 persona_id IS NULL 的记录：{len(open_rows)}")
    for r in open_rows:
        print(f"  [{r['position_id']}] {r['symbol']} {r['name'] or ''} "
              f"qty={r['current_qty']} entry={r['entry_price']} date={r['entry_date']}")

    # 2. 预览 closed 持仓
    closed_count = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE persona_id IS NULL AND status != 'open'"
    ).fetchone()[0]
    print(f"\nclosed 持仓中 persona_id IS NULL 的记录：{closed_count}")

    # 3. 预览 trade_signals
    sig_count = conn.execute(
        "SELECT COUNT(*) FROM trade_signals WHERE persona_id IS NULL"
    ).fetchone()[0]
    print(f"trade_signals 中 persona_id IS NULL 的记录：{sig_count}")

    if args.dry_run:
        print("\n[DRY-RUN] 不执行更新。")
        conn.close()
        return

    # 4. 执行更新
    print("\n开始回填...")

    if args.include_closed:
        cur = conn.execute(
            "UPDATE positions SET persona_id = ? WHERE persona_id IS NULL",
            (args.default_persona,),
        )
        print(f"positions 更新: {cur.rowcount} 行")
    else:
        cur = conn.execute(
            "UPDATE positions SET persona_id = ? WHERE persona_id IS NULL AND status = 'open'",
            (args.default_persona,),
        )
        print(f"positions (仅 open) 更新: {cur.rowcount} 行")

    cur = conn.execute(
        "UPDATE trade_signals SET persona_id = ? WHERE persona_id IS NULL",
        (args.default_persona,),
    )
    print(f"trade_signals 更新: {cur.rowcount} 行")

    conn.commit()
    conn.close()
    print("\n✅ 回填完成")


if __name__ == "__main__":
    main()
