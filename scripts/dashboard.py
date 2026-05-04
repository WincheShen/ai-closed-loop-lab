"""Phase 3.5 S4 — minimal Streamlit dashboard.

Run:
    streamlit run scripts/dashboard.py --server.port 8501

This is a read-only view over the central_brain.db:
- Tab 1  今日/历史选股
- Tab 2  LLM 成本与调用
- Tab 3  社媒发帖追踪

Everything is best-effort: missing tables or empty tables render as
friendly "no data yet" messages rather than raising.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

# Resolve DB path the same way MemoryStore does (cfg().get("db_path"))
try:
    from src.infra.config import cfg

    DB_PATH = cfg().get("db_path") or "data/central_brain.db"
except Exception:
    DB_PATH = "data/central_brain.db"


st.set_page_config(page_title="AI Closed Loop — Dashboard", layout="wide")
st.title("AI Closed Loop Lab — 运营 Dashboard")
st.caption(f"central_brain.db → `{DB_PATH}`")

if not Path(DB_PATH).exists():
    st.warning(
        "central_brain.db 尚未生成。跑一次 `scripts/run_daily_scan.py` "
        "或任意接入了 central_brain 的流程即可产生。"
    )
    st.stop()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


conn = _connect()

tab1, tab2, tab3 = st.tabs(["选股归档", "LLM 成本", "社媒发帖"])

# ---------------------------------------------------------------------------
# Tab 1 — daily picks
# ---------------------------------------------------------------------------
with tab1:
    st.subheader("每日选股归档 (daily_picks_archive)")
    if not _table_exists(conn, "daily_picks_archive"):
        st.info("daily_picks_archive 表不存在 — 还没跑过一次接入了 central_brain 的 daily_scan。")
    else:
        rows = _rows(
            conn,
            "SELECT pick_date, is_mock_data, candidates_count, agent_calls_count, "
            "total_llm_cost_usd, elapsed_seconds, created_at "
            "FROM daily_picks_archive ORDER BY pick_date DESC LIMIT 30",
        )
        if not rows:
            st.info("目前还没有任何归档记录。")
        else:
            st.dataframe(rows, use_container_width=True)

            latest = rows[0]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("最新日期", latest["pick_date"])
            c2.metric("候选数", latest["candidates_count"])
            c3.metric("Agent 调用", latest["agent_calls_count"])
            c4.metric("LLM 成本 USD", round(latest["total_llm_cost_usd"] or 0, 3))

            # Detail of latest day
            detail_row = conn.execute(
                "SELECT hot_sectors_json, aggressive_json, stable_json "
                "FROM daily_picks_archive WHERE pick_date = ?",
                (latest["pick_date"],),
            ).fetchone()
            if detail_row:
                st.markdown("#### 热点板块")
                try:
                    st.write(json.loads(detail_row["hot_sectors_json"] or "[]"))
                except Exception:
                    st.text(detail_row["hot_sectors_json"])

                col_a, col_s = st.columns(2)
                with col_a:
                    st.markdown("#### 🔥 激进推荐")
                    try:
                        st.dataframe(json.loads(detail_row["aggressive_json"] or "[]"))
                    except Exception:
                        st.text(detail_row["aggressive_json"])
                with col_s:
                    st.markdown("#### 🛡 稳健推荐")
                    try:
                        st.dataframe(json.loads(detail_row["stable_json"] or "[]"))
                    except Exception:
                        st.text(detail_row["stable_json"])

# ---------------------------------------------------------------------------
# Tab 2 — LLM cost
# ---------------------------------------------------------------------------
with tab2:
    st.subheader("LLM 调用打点 (llm_calls)")
    if not _table_exists(conn, "llm_calls"):
        st.info("llm_calls 表不存在 — 还没跑过 tradingagents 分析。")
    else:
        total = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(total_tokens),0) AS t, "
            "COALESCE(SUM(cost_usd),0) AS c FROM llm_calls"
        ).fetchone()
        c1, c2, c3 = st.columns(3)
        c1.metric("累计调用数", total["n"])
        c2.metric("累计 token 数", total["t"])
        c3.metric("累计成本 USD", round(total["c"], 3))

        by_model = _rows(
            conn,
            "SELECT model, COUNT(*) AS calls, "
            "SUM(prompt_tokens) AS prompt_tokens, "
            "SUM(completion_tokens) AS completion_tokens, "
            "ROUND(SUM(cost_usd), 4) AS cost_usd "
            "FROM llm_calls GROUP BY model ORDER BY cost_usd DESC",
        )
        if by_model:
            st.markdown("#### 按 model 汇总")
            st.dataframe(by_model, use_container_width=True)

        by_stage = _rows(
            conn,
            "SELECT COALESCE(stage,'?') AS stage, COUNT(*) AS calls, "
            "ROUND(SUM(cost_usd), 4) AS cost_usd, "
            "ROUND(AVG(latency_ms), 0) AS avg_latency_ms "
            "FROM llm_calls GROUP BY stage ORDER BY cost_usd DESC",
        )
        if by_stage:
            st.markdown("#### 按 stage 汇总")
            st.dataframe(by_stage, use_container_width=True)

        recent = _rows(
            conn,
            "SELECT ts, symbol, stage, model, prompt_tokens, completion_tokens, "
            "ROUND(cost_usd, 4) AS cost_usd, latency_ms, success, error_msg "
            "FROM llm_calls ORDER BY id DESC LIMIT 50",
        )
        if recent:
            st.markdown("#### 最近 50 次调用")
            st.dataframe(recent, use_container_width=True)

# ---------------------------------------------------------------------------
# Tab 3 — social posts
# ---------------------------------------------------------------------------
with tab3:
    st.subheader("社媒发帖追踪 (social_posts)")
    if not _table_exists(conn, "social_posts"):
        st.info("social_posts 表不存在 — 还没 dispatch 过 SMA 任务。")
    else:
        rows = _rows(
            conn,
            "SELECT sma_task_id, account_id, platform, source_pick_date, "
            "dispatched_at, sma_status, post_url, published_at "
            "FROM social_posts ORDER BY dispatched_at DESC LIMIT 100",
        )
        if not rows:
            st.info("目前还没有任何 social_posts 记录。")
        else:
            by_account: dict[str, int] = {}
            for r in rows:
                by_account[r["account_id"]] = by_account.get(r["account_id"], 0) + 1

            st.markdown("#### 按账号分组")
            st.dataframe(
                [{"account_id": k, "posts": v} for k, v in by_account.items()],
                use_container_width=True,
            )

            st.markdown("#### 最近 100 条")
            st.dataframe(rows, use_container_width=True)


conn.close()
