#!/usr/bin/env python3
"""从历史 fills 表推导并创建持仓记录（一次性迁移脚本）。"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

DB_PATH = "/app/data/central_brain.db"


def migrate_fills_to_positions() -> None:
    """从 fills 表推导持仓并写入 positions 表。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 获取所有 fills
    fills = conn.execute(
        "SELECT * FROM fills ORDER BY filled_at"
    ).fetchall()

    if not fills:
        print("📭 无历史成交记录")
        return

    print(f"📊 找到 {len(fills)} 笔历史成交，开始推导持仓...")

    # 按股票分组，计算持仓
    positions: dict[str, dict] = {}
    for fill in fills:
        symbol = fill["symbol"]
        side = fill["side"]
        qty = fill["quantity"]
        price = fill["avg_price"]
        filled_at = fill["filled_at"]

        if symbol not in positions:
            positions[symbol] = {
                "symbol": symbol,
                "name": symbol,  # fills 表没有 name 字段，用 symbol 代替
                "entry_price": 0.0,
                "current_qty": 0,
                "entry_date": filled_at[:10],
                "total_cost": 0.0,
            }

        pos = positions[symbol]
        if side == "buy":
            pos["current_qty"] += qty
            pos["total_cost"] += qty * price
        elif side == "sell":
            pos["current_qty"] -= qty
            # 卖出不调整成本，只减少数量

    # 计算移动平均成本
    for sym, pos in positions.items():
        if pos["current_qty"] > 0:
            pos["entry_price"] = round(pos["total_cost"] / pos["current_qty"], 2)
        else:
            # 已清仓，跳过
            del positions[sym]

    # 写入 positions 表
    created = 0
    for sym, pos in positions.items():
        position_id = f"POS-MIG-{uuid.uuid4().hex[:8].upper()}"
        try:
            conn.execute(
                """INSERT INTO positions
                (position_id, symbol, name, side, entry_price, current_qty,
                 entry_date, status, original_thesis, created_at)
                VALUES (?, ?, ?, 'long', ?, ?, ?, 'open', '从历史成交迁移', ?)""",
                (
                    position_id, pos["symbol"], pos["name"],
                    pos["entry_price"], pos["current_qty"],
                    pos["entry_date"], datetime.now().isoformat(),
                ),
            )
            created += 1
            print(f"  ✅ {sym} | 成本 {pos['entry_price']:.2f} | 数量 {pos['current_qty']}")
        except Exception as e:
            print(f"  ❌ {sym} 失败: {e}")

    conn.commit()
    print(f"\n✅ 迁移完成: 创建 {created} 只持仓")

    # 验证
    rows = conn.execute("SELECT * FROM positions WHERE status='open'").fetchall()
    print(f"📊 当前持仓总数: {len(rows)}")


if __name__ == "__main__":
    migrate_fills_to_positions()
