#!/usr/bin/env python3
"""查询 Central Brain — Cognitive Agent 决策可视化工具。

用法:
    python scripts/query_brain.py                # 默认: 展示完整摘要
    python scripts/query_brain.py positions      # 当前持仓
    python scripts/query_brain.py reviews        # 持仓复审记录
    python scripts/query_brain.py regime         # MarketBrain 历史判定
    python scripts/query_brain.py signals        # 最近交易信号
    python scripts/query_brain.py risk           # 最近风控裁决
    python scripts/query_brain.py events         # 最近 agent 事件
    python scripts/query_brain.py today          # 今日完整决策链路
    python scripts/query_brain.py session <id>   # 某次 session 的完整链路

环境:
    DB_PATH=/path/to/central_brain.db (默认尝试 /app/data 和 data/)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path


# Resolve DB path: env > docker container > local dev
def _resolve_db_path() -> str:
    if os.environ.get("DB_PATH"):
        return os.environ["DB_PATH"]
    for p in ("/app/data/central_brain.db", "data/central_brain.db"):
        if Path(p).exists():
            return p
    return "data/central_brain.db"


DB_PATH = _resolve_db_path()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _truncate(s: str, n: int = 80) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _fmt_ts(ts: str | None) -> str:
    return (ts or "?")[:19]


# ─────────────────────────────────────────────────────────────────────────────
# Queries
# ─────────────────────────────────────────────────────────────────────────────

def query_positions() -> None:
    """当前持仓 (含认知元数据)。"""
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM positions WHERE status='open' ORDER BY entry_date DESC"
    ).fetchall()

    if not rows:
        print("📭 当前无持仓")
        return

    print(f"📊 当前持仓 ({len(rows)} 只)")
    print("=" * 90)
    for r in rows:
        d = dict(r)
        print(f"  {d.get('symbol')} {d.get('name') or ''} [{d.get('position_id')}]")
        print(f"    成本={d.get('entry_price'):.2f} 数量={d.get('current_qty')} "
              f"入场日={d.get('entry_date')}")
        print(f"    策略={d.get('original_strategy') or 'n/a'} | "
              f"regime={d.get('market_regime') or 'n/a'} | "
              f"sector={d.get('sector') or 'n/a'} | "
              f"persona={d.get('persona_version') or 'n/a'}")
        print(f"    目标={d.get('target_price')} 止损={d.get('stop_loss')}")
        print(f"    Thesis: {_truncate(d.get('original_thesis') or '', 100)}")
        if d.get('bull_case'):
            print(f"    看多: {_truncate(d['bull_case'], 100)}")
        if d.get('bear_case'):
            print(f"    风险: {_truncate(d['bear_case'], 100)}")
        if d.get('last_review_at'):
            print(f"    最近复审: {_fmt_ts(d['last_review_at'])} "
                  f"动作={d.get('last_review_action')} "
                  f"理由={_truncate(d.get('last_review_reason') or '', 60)}")
        print()


def query_reviews(limit: int = 15) -> None:
    """持仓复审记录。"""
    conn = _conn()
    rows = conn.execute(
        """SELECT pr.*, p.symbol, p.name
        FROM position_reviews pr
        LEFT JOIN positions p ON pr.position_id = p.position_id
        ORDER BY pr.review_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()

    if not rows:
        print("📭 暂无复审记录")
        return

    print(f"📝 持仓复审 (最新 {len(rows)} 条)")
    print("=" * 90)
    for r in rows:
        d = dict(r)
        ts = _fmt_ts(d.get('review_at'))
        pnl = d.get('pnl_pct')
        pnl_s = f"{pnl:+.2f}%" if pnl is not None else "n/a"
        print(f"  [{ts}] {d.get('symbol')} {d.get('name') or ''}")
        print(f"    动作={d.get('action')} 现价={d.get('current_price')} 盈亏={pnl_s}")
        print(f"    理由: {_truncate(d.get('reason') or '', 100)}")
        print()


def query_regime(limit: int = 15) -> None:
    """MarketBrain 历史 regime 判定。"""
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM market_regime_snapshots ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

    if not rows:
        print("📭 暂无 MarketBrain 判定记录")
        return

    print(f"🧠 MarketBrain 历史判定 (最新 {len(rows)} 条)")
    print("=" * 90)
    for r in rows:
        d = dict(r)
        ts = _fmt_ts(d.get('created_at'))
        try:
            hot = json.loads(d.get('hot_sectors_json') or '[]')
        except Exception:
            hot = []
        try:
            bias = json.loads(d.get('strategy_bias_json') or '{}')
        except Exception:
            bias = {}
        max_pos = d.get('max_total_position_pct')
        max_pos_s = f"{max_pos:.0%}" if isinstance(max_pos, (int, float)) else "n/a"
        print(f"  [{ts}] regime={d.get('regime')} "
              f"posture={d.get('recommended_posture')} "
              f"appetite={d.get('risk_appetite')} "
              f"max_pos={max_pos_s}")
        if hot:
            print(f"    热点 Top: {', '.join(hot[:5])}")
        if bias:
            print(f"    策略偏好: " + ", ".join(f"{k}={v:.2f}" for k, v in bias.items()))
        print(f"    persona={d.get('persona_version') or 'n/a'} "
              f"mock={'Y' if d.get('is_mock_data') else 'N'}")
        print(f"    总结: {_truncate(d.get('summary') or '', 100)}")
        print()


def query_signals(limit: int = 20) -> None:
    """最近交易信号 (含认知元数据)。"""
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM trade_signals ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()

    if not rows:
        print("📭 暂无交易信号")
        return

    print(f"💡 交易信号 (最新 {len(rows)} 条)")
    print("=" * 90)
    for r in rows:
        d = dict(r)
        ts = _fmt_ts(d.get('timestamp'))
        ap = d.get('approved_position_pct')
        ap_s = f"{ap:.0%}" if isinstance(ap, (int, float)) else "未风控"
        op = d.get('position_pct') or 0
        rd = d.get('risk_decision') or 'pending'
        print(f"  [{ts}] {d.get('symbol')} {d.get('action')} "
              f"@ {d.get('entry_price')} | status={d.get('status')}")
        print(f"    策略={d.get('strategy')} | regime={d.get('market_regime') or 'n/a'} "
              f"| persona={d.get('persona_version') or 'n/a'}")
        print(f"    入场={d.get('entry_price')} 目标={d.get('target_price')} "
              f"止损={d.get('stop_loss')}")
        print(f"    仓位: 原始={op:.0%} 风控后={ap_s} 裁决={rd}")
        print(f"    Thesis: {_truncate(d.get('rationale') or '', 100)}")
        print()


def query_risk(limit: int = 30) -> None:
    """最近风控裁决。"""
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM risk_decisions ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

    if not rows:
        print("📭 暂无风控记录")
        return

    print(f"🛡️  风控裁决 (最新 {len(rows)} 条)")
    print("=" * 90)
    # 统计
    approve = sum(1 for r in rows if r['decision'] == 'approve')
    reduce = sum(1 for r in rows if r['decision'] == 'reduce')
    reject = sum(1 for r in rows if r['decision'] == 'reject')
    print(f"  统计: approve={approve} reduce={reduce} reject={reject}")
    print()

    for r in rows:
        d = dict(r)
        ts = _fmt_ts(d.get('created_at'))
        tag = {"approve": "✓", "reduce": "△", "reject": "✗"}.get(d.get('decision'), "?")
        op = d.get('original_position_pct') or 0
        ap = d.get('approved_position_pct') or 0
        try:
            flags = json.loads(d.get('risk_flags_json') or '[]')
        except Exception:
            flags = []
        print(f"  {tag} [{ts}] {d.get('symbol')} signal={d.get('signal_id')}")
        print(f"    {op:.0%} → {ap:.0%} | regime={d.get('market_regime')} "
              f"| flags={','.join(flags) or '无'}")
        print(f"    理由: {_truncate(d.get('reason') or '', 100)}")
        print()


def query_events(limit: int = 25) -> None:
    """最近 agent 事件。"""
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM events ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

    if not rows:
        print("📭 暂无事件")
        return

    print(f"🔔 Agent 事件 (最新 {len(rows)} 条)")
    print("=" * 90)
    for r in rows:
        d = dict(r)
        ts = _fmt_ts(d.get('created_at'))
        print(f"  [{ts}] {d.get('agent')} → {d.get('event_type')}")
        if d.get('payload'):
            try:
                p = json.loads(d['payload'])
                if isinstance(p, dict):
                    keys = ['symbol', 'action', 'regime', 'posture', 'candidate_count',
                            'buy_count', 'symbols', 'is_mock']
                    summary = {k: v for k, v in p.items() if k in keys}
                    if summary:
                        print(f"    {summary}")
            except Exception:
                pass
        print()


def query_today() -> None:
    """今日完整决策链路。"""
    today = date.today().isoformat()
    conn = _conn()

    print(f"📅 今日决策链路 ({today})")
    print("=" * 90)

    # 1. MarketBrain
    rows = conn.execute(
        "SELECT * FROM market_regime_snapshots WHERE date(created_at)=? "
        "ORDER BY created_at",
        (today,),
    ).fetchall()
    print(f"\n[1] MarketBrain 判定 ({len(rows)} 次)")
    print("-" * 90)
    for r in rows:
        d = dict(r)
        print(f"  [{_fmt_ts(d['created_at'])[11:16]}] "
              f"regime={d['regime']} posture={d.get('recommended_posture')} "
              f"max_pos={(d.get('max_total_position_pct') or 0):.0%}")

    # 2. Signals
    rows = conn.execute(
        "SELECT * FROM trade_signals WHERE date(timestamp)=? ORDER BY timestamp",
        (today,),
    ).fetchall()
    print(f"\n[2] 交易信号 ({len(rows)} 条)")
    print("-" * 90)
    for r in rows:
        d = dict(r)
        print(f"  [{_fmt_ts(d['timestamp'])[11:16]}] {d['symbol']} "
              f"{d['action']} | strategy={d.get('strategy')} | "
              f"risk={d.get('risk_decision') or 'pending'} | status={d['status']}")

    # 3. Risk
    rows = conn.execute(
        "SELECT * FROM risk_decisions WHERE date(created_at)=? ORDER BY created_at",
        (today,),
    ).fetchall()
    if rows:
        print(f"\n[3] 风控裁决 ({len(rows)} 条)")
        print("-" * 90)
        for r in rows:
            d = dict(r)
            tag = {"approve": "✓", "reduce": "△", "reject": "✗"}.get(d['decision'], "?")
            print(f"  {tag} {d['symbol']} {(d.get('original_position_pct') or 0):.0%}"
                  f" → {(d.get('approved_position_pct') or 0):.0%} "
                  f"| {_truncate(d.get('reason') or '', 60)}")

    # 4. Today's fills
    rows = conn.execute(
        "SELECT f.*, o.signal_id FROM fills f "
        "LEFT JOIN orders o ON f.order_id=o.order_id "
        "WHERE date(f.filled_at)=? ORDER BY f.filled_at",
        (today,),
    ).fetchall()
    print(f"\n[4] 今日成交 ({len(rows)} 笔)")
    print("-" * 90)
    for r in rows:
        d = dict(r)
        print(f"  [{_fmt_ts(d['filled_at'])[11:16]}] {d['symbol']} "
              f"{d['side']} qty={d['quantity']} @ {d['avg_price']}")

    # 5. Today's reviews
    rows = conn.execute(
        "SELECT pr.*, p.symbol FROM position_reviews pr "
        "LEFT JOIN positions p ON pr.position_id=p.position_id "
        "WHERE date(pr.review_at)=? ORDER BY pr.review_at",
        (today,),
    ).fetchall()
    if rows:
        print(f"\n[5] 今日复审 ({len(rows)} 次)")
        print("-" * 90)
        for r in rows:
            d = dict(r)
            print(f"  [{_fmt_ts(d['review_at'])[11:16]}] {d.get('symbol')} "
                  f"action={d['action']} | {_truncate(d.get('reason') or '', 60)}")

    print()


def query_session(session_id: str) -> None:
    """某次 session 的完整链路。"""
    conn = _conn()
    print(f"🔍 Session: {session_id}")
    print("=" * 90)

    # regime
    rows = conn.execute(
        "SELECT * FROM market_regime_snapshots WHERE session_id=?",
        (session_id,),
    ).fetchall()
    for r in rows:
        d = dict(r)
        print(f"\n[MarketBrain] regime={d['regime']} posture={d['recommended_posture']}")
        print(f"  summary: {d.get('summary')}")

    # signals
    rows = conn.execute(
        "SELECT * FROM trade_signals WHERE session_id=? ORDER BY timestamp",
        (session_id,),
    ).fetchall()
    print(f"\n[Strategist] 信号 {len(rows)} 条")
    for r in rows:
        d = dict(r)
        print(f"  {d['symbol']} {d['action']} @ {d['entry_price']} | "
              f"strategy={d.get('strategy')} | risk={d.get('risk_decision') or 'pending'}")
        print(f"    Thesis: {_truncate(d.get('rationale') or '', 100)}")

    # risk decisions
    rows = conn.execute(
        "SELECT * FROM risk_decisions WHERE session_id=? ORDER BY created_at",
        (session_id,),
    ).fetchall()
    if rows:
        print(f"\n[RiskGovernor] 裁决 {len(rows)} 条")
        for r in rows:
            d = dict(r)
            tag = {"approve": "✓", "reduce": "△", "reject": "✗"}.get(d['decision'], "?")
            print(f"  {tag} {d['symbol']} {(d.get('original_position_pct') or 0):.0%} "
                  f"→ {(d.get('approved_position_pct') or 0):.0%} | "
                  f"{_truncate(d.get('reason') or '', 80)}")

    # events
    rows = conn.execute(
        "SELECT * FROM events WHERE session_id=? ORDER BY created_at",
        (session_id,),
    ).fetchall()
    print(f"\n[Events] {len(rows)} 条")
    for r in rows:
        d = dict(r)
        print(f"  [{_fmt_ts(d['created_at'])[11:19]}] {d['agent']} → {d['event_type']}")

    print()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def _print_header() -> None:
    print("=" * 90)
    print(f"Central Brain 查询工具")
    print(f"DB: {DB_PATH}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 90)


def main() -> None:
    if not Path(DB_PATH).exists():
        print(f"❌ 数据库不存在: {DB_PATH}")
        sys.exit(1)

    _print_header()
    print()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "positions":
            query_positions()
        elif cmd == "reviews":
            query_reviews()
        elif cmd == "regime":
            query_regime()
        elif cmd == "signals":
            query_signals()
        elif cmd == "risk":
            query_risk()
        elif cmd == "events":
            query_events()
        elif cmd == "today":
            query_today()
        elif cmd == "session" and len(sys.argv) > 2:
            query_session(sys.argv[2])
        else:
            print(f"未知命令: {cmd}")
            print(__doc__)
    else:
        # 默认: 当日概览
        query_today()
        print()
        query_positions()


if __name__ == "__main__":
    main()
