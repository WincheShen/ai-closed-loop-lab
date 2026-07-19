"""针对 list_open_positions 的 persona 过滤行为的回归测试。

背景：早期版本入库的 positions 可能 persona_id 为 NULL（旧 schema，没有 NOT NULL 约束）。
盘中复审按人格循环调用 list_open_positions(persona_id=xxx) 时，
严格匹配会漏掉 NULL 持仓 → 出现"买了不卖"的 bug。

参见 docs/roadmap_v2.md P0-1。

本文件构造一个"模拟旧 schema"的临时 DB（persona_id 无 NOT NULL 约束），
直接验证修复后的 SQL 逻辑。
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest


def _create_legacy_db(path: Path) -> None:
    """建一个模拟旧 schema 的 DB：positions 表 persona_id 允许 NULL。"""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE positions (
            position_id TEXT PRIMARY KEY,
            persona_id TEXT,
            symbol TEXT NOT NULL,
            name TEXT,
            side TEXT DEFAULT 'long',
            entry_price REAL NOT NULL,
            current_qty INTEGER,
            entry_date TEXT,
            status TEXT DEFAULT 'open',
            original_signal_id TEXT,
            original_thesis TEXT,
            original_strategy TEXT,
            bull_case TEXT,
            bear_case TEXT,
            target_price REAL,
            stop_loss REAL,
            created_at TEXT,
            market_regime TEXT,
            persona_version TEXT,
            sector TEXT,
            closed_at TEXT,
            close_price REAL,
            realized_pnl REAL,
            last_review_at TEXT,
            last_review_action TEXT,
            last_review_reason TEXT,
            account_id TEXT
        )"""
    )
    conn.commit()

    # 老数据：铜陵有色 persona_id=NULL
    conn.execute(
        "INSERT INTO positions (position_id, persona_id, symbol, name, entry_price, "
        "current_qty, entry_date, status) VALUES (?, NULL, ?, ?, ?, ?, ?, 'open')",
        ("POS-NULL", "000630", "铜陵有色", 7.66, 3100, "2026-06-15"),
    )
    # 三只指派了人格的持仓
    for i, pid in enumerate([
        "short_term_hot_rotation_v1", "duan_yongping_v1", "warren_buffett_v1"
    ]):
        conn.execute(
            "INSERT INTO positions (position_id, persona_id, symbol, entry_price, "
            "current_qty, entry_date, status) VALUES (?, ?, ?, ?, ?, ?, 'open')",
            (f"POS-{i}", pid, f"00000{i}", 10.0, 100, "2026-07-01"),
        )
    conn.commit()
    conn.close()


def _query_open_positions(
    db_path: str,
    persona_id: str | None,
    include_unassigned: bool = False,
) -> list[dict]:
    """直接复用 MemoryStore.list_open_positions 的 SQL 语义。

    我们不 import MemoryStore（会触发新 schema 建表），而是复现修复后的查询逻辑。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if persona_id:
        if include_unassigned:
            rows = conn.execute(
                "SELECT * FROM positions WHERE status = 'open' "
                "AND (persona_id = ? OR persona_id IS NULL) "
                "ORDER BY entry_date",
                (persona_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM positions WHERE status = 'open' AND persona_id = ? "
                "ORDER BY entry_date",
                (persona_id,),
            ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status = 'open' ORDER BY entry_date"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@pytest.fixture
def legacy_db():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "legacy.db"
        _create_legacy_db(db)
        yield str(db)


def test_persona_filter_excludes_null_by_default(legacy_db):
    """默认行为：list_open_positions(persona_id='xxx') 只返回该人格的持仓，
    不返回 persona_id IS NULL 的历史持仓。这是修复前的行为，需要保持向后兼容。"""
    rows = _query_open_positions(legacy_db, persona_id="short_term_hot_rotation_v1")
    symbols = [r["symbol"] for r in rows]
    assert "000630" not in symbols, "默认过滤应排除 NULL 持仓"
    assert len(rows) == 1


def test_persona_filter_includes_null_when_flag_set(legacy_db):
    """include_unassigned=True：同时返回 NULL 持仓 + 该人格持仓。
    这是修复"买了不卖" bug 的关键路径。"""
    rows = _query_open_positions(
        legacy_db,
        persona_id="short_term_hot_rotation_v1",
        include_unassigned=True,
    )
    symbols = [r["symbol"] for r in rows]
    assert "000630" in symbols, "include_unassigned=True 时必须能拿到 NULL 持仓"
    assert len(rows) == 2


def test_no_persona_filter_returns_all(legacy_db):
    """persona_id=None → 返回所有 open 持仓（含 NULL）。这是老行为，保持不变。"""
    rows = _query_open_positions(legacy_db, persona_id=None)
    assert len(rows) == 4  # 1 null + 3 personas


def test_include_unassigned_ignored_when_no_persona(legacy_db):
    """persona_id=None 时 include_unassigned 参数无意义，行为与不传相同。"""
    rows = _query_open_positions(
        legacy_db, persona_id=None, include_unassigned=True,
    )
    assert len(rows) == 4
