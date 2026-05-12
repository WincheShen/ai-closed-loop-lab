#!/usr/bin/env python3
"""查询 Central Brain 中的持仓和复审记录。"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = "/app/data/central_brain.db"


def query_positions() -> None:
    """查询当前持仓。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM positions WHERE status='open' ORDER BY entry_date"
    ).fetchall()

    if not rows:
        print("📭 无持仓")
        return

    print(f"📊 当前持仓 ({len(rows)} 只):")
    print("-" * 80)
    for r in rows:
        print(f"  {r['symbol']} {r.get('name', 'N/A')}")
        print(f"    成本: {r['entry_price']:.2f} | 数量: {r['current_qty']} | "
              f"入场: {r['entry_date']}")
        print(f"    Thesis: {r.get('original_thesis', 'N/A')[:60]}")
        print(f"    上次复审: {r.get('last_review_at', 'N/A')} | "
              f"动作: {r.get('last_review_action', 'N/A')}")
        print()
    conn.close()


def query_reviews(limit: int = 10) -> None:
    """查询最近复审记录。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM position_reviews ORDER BY review_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

    if not rows:
        print("📭 无复审记录")
        return

    print(f"📝 最近复审记录 (最新 {len(rows)} 条):")
    print("-" * 80)
    for r in rows:
        pos = conn.execute(
            "SELECT symbol, name FROM positions WHERE position_id=?",
            (r['position_id'],),
        ).fetchone()
        sym = pos['symbol'] if pos else "?"
        name = pos['name'] if pos else ""
        ts = r['review_at'][:16] if r['review_at'] else "?"
        print(f"  [{ts}] {sym} {name}")
        print(f"    动作: {r['action']} | 价格: {r.get('current_price', '?')} | "
              f"盈亏: {r.get('pnl_pct', '?')}%")
        print(f"    理由: {r.get('reason', 'N/A')[:80]}")
        print()
    conn.close()


def query_events(limit: int = 20) -> None:
    """查询最近事件。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM events ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

    if not rows:
        print("📭 无事件记录")
        return

    print(f"🔔 最近事件 (最新 {len(rows)} 条):")
    print("-" * 80)
    for r in rows:
        ts = r['created_at'][:19] if r['created_at'] else "?"
        print(f"  [{ts}] {r['agent']} - {r['event_type']}")
        if r['payload']:
            import json
            try:
                payload = json.loads(r['payload'])
                if isinstance(payload, dict):
                    # 只显示关键字段
                    keys = ['action', 'symbol', 'total_positions', 'actions_taken']
                    summary = {k: v for k, v in payload.items() if k in keys}
                    print(f"    {summary}")
                else:
                    print(f"    {str(payload)[:100]}")
            except:
                print(f"    {r['payload'][:100]}")
        print()
    conn.close()


def main() -> None:
    if not Path(DB_PATH).exists():
        print(f"❌ 数据库不存在: {DB_PATH}")
        sys.exit(1)

    print("=" * 80)
    print(f"Central Brain 查询工具")
    print(f"数据库: {DB_PATH}")
    print(f"时间: {datetime.now()}")
    print("=" * 80)
    print()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "positions":
            query_positions()
        elif cmd == "reviews":
            query_reviews()
        elif cmd == "events":
            query_events()
        else:
            print(f"未知命令: {cmd}")
            print("用法: python query_brain.py [positions|reviews|events]")
    else:
        # 默认显示全部
        query_positions()
        print()
        query_reviews(limit=5)
        print()
        query_events(limit=10)


if __name__ == "__main__":
    main()
