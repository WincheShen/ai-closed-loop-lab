"""Webhook Listener — FastAPI 入口。

接收沈经理通过 wechat/飞书 推送的交易记录（图片+文字），
进行：
    1. 落库（trade_records）
    2. 文字合规处理
    3. 图片脱敏
    4. 发布事件 trade_record_received（供 Stock Analyzer 复盘 + Social Media 创作消费）

启动：
    uvicorn webhook_listener.server:app --port 8002 --reload
"""
from __future__ import annotations

import asyncio
import glob
import json
import logging
import math
import os
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


class SafeJSONEncoder(json.JSONEncoder):
    """处理 inf/nan 等特殊浮点值的 JSON encoder。"""
    def default(self, obj):
        if isinstance(obj, float):
            if math.isinf(obj) or math.isnan(obj):
                return None
        return super().default(obj)


def clean_special_floats(obj):
    """递归清理数据中的特殊浮点值（inf/nan）。"""
    if isinstance(obj, float):
        if math.isinf(obj) or math.isnan(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: clean_special_floats(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [clean_special_floats(item) for item in obj]
    else:
        return obj

from src.infra.config import cfg
from .image_redactor import ImageRedactor
from .text_compliance import ComplianceResult, sanitize_text

# Phase 3: Webhook → SMA 自动触发
_auto_dispatch_enabled = os.environ.get("WEBHOOK_AUTO_SMA_DISPATCH", "false").lower() in ("1", "true", "yes")
_sma_default_account = os.environ.get("WEBHOOK_SMA_DEFAULT_ACCOUNT", "XHS_01")

# 延迟导入避免循环依赖
def _get_topic_router():
    from social_media_dispatcher.topic_router import TopicRouter
    return TopicRouter()

def _get_sma_client():
    from social_media_dispatcher.client import SmaClient
    return SmaClient()

def _get_central_brain():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from central_brain.metadata_store import get_central_brain
    return get_central_brain()

logger = logging.getLogger(__name__)


_DATA_ROOT = Path(os.environ.get("WEBHOOK_DATA_ROOT", "data/webhook"))
_DATA_ROOT.mkdir(parents=True, exist_ok=True)
(_DATA_ROOT / "raw").mkdir(exist_ok=True)
(_DATA_ROOT / "redacted").mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Lightweight SQLite log
# ---------------------------------------------------------------------------

_DB_PATH = _DATA_ROOT / "trade_records.sqlite"
_conn = sqlite3.connect(_DB_PATH, check_same_thread=False, isolation_level=None, timeout=30)
_conn.execute("PRAGMA journal_mode=WAL")
_conn.execute("PRAGMA synchronous=NORMAL")
_conn.execute("PRAGMA busy_timeout=5000")
_conn.executescript("""
CREATE TABLE IF NOT EXISTS trade_records (
    id              TEXT PRIMARY KEY,
    received_at     TEXT NOT NULL,
    source          TEXT NOT NULL,
    raw_text        TEXT,
    safe_text       TEXT,
    forbidden_hits  TEXT,
    is_publishable  INTEGER NOT NULL,
    raw_image_path  TEXT,
    redacted_image_path TEXT,
    metadata_json   TEXT
);
""")


app = FastAPI(
    title="AI Lab Webhook Listener",
    description="接收 wechat/飞书 推送的交易记录并做合规预处理",
    version="0.1.0",
)
redactor = ImageRedactor()

try:
    from stock_analyzer.strategy.api import router as strategy_router
    app.include_router(strategy_router)
except Exception as _e:
    logger.warning("Strategy API router not available: %s", _e)


class TradeRecordResponse(BaseModel):
    record_id: str
    received_at: datetime
    source: str
    safe_text: str
    is_publishable: bool
    forbidden_hits: list[str]
    raw_image_path: Optional[str]
    redacted_image_path: Optional[str]


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "webhook_listener"}


@app.post("/webhook/trade", response_model=TradeRecordResponse)
async def receive_trade(
    text: str = Form(""),
    source: str = Form("manual"),
    image: Optional[UploadFile] = File(None),
) -> TradeRecordResponse:

    if not text and not image:
        raise HTTPException(status_code=400, detail="text and image cannot both be empty")

    record_id = uuid.uuid4().hex[:12]
    received_at = datetime.now()

    compliance: ComplianceResult = sanitize_text(text)

    raw_image_path: Optional[Path] = None
    redacted_image_path: Optional[Path] = None

    if image is not None:
        suffix = Path(image.filename or "img.png").suffix or ".png"
        raw_image_path = _DATA_ROOT / "raw" / f"{record_id}{suffix}"

        with raw_image_path.open("wb") as f:
            f.write(await image.read())

        redacted_image_path = _DATA_ROOT / "redacted" / f"{record_id}_safe{suffix}"

        try:
            redactor.redact(raw_image_path, redacted_image_path)
        except Exception:
            logger.exception("image redact failed for %s", record_id)
            redacted_image_path = None

    import json as _json
    _conn.execute(
        """
        INSERT INTO trade_records (
            id, received_at, source, raw_text, safe_text,
            forbidden_hits, is_publishable,
            raw_image_path, redacted_image_path, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            received_at.isoformat(),
            source,
            text,
            compliance.safe_text,
            _json.dumps(compliance.forbidden_hits, ensure_ascii=False),
            int(compliance.is_publishable),
            str(raw_image_path) if raw_image_path else None,
            str(redacted_image_path) if redacted_image_path else None,
            "{}",
        ),
    )

    return TradeRecordResponse(
        record_id=record_id,
        received_at=received_at,
        source=source,
        safe_text=compliance.safe_text,
        is_publishable=compliance.is_publishable,
        forbidden_hits=compliance.forbidden_hits,
        raw_image_path=str(raw_image_path) if raw_image_path else None,
        redacted_image_path=str(redacted_image_path) if redacted_image_path else None,
    )


@app.get("/webhook/records/recent")
async def recent_records(limit: int = 10):
    """返回最近的交易记录（供前端展示）。"""
    rows = _conn.execute(
        "SELECT id, received_at, source, safe_text, is_publishable "
        "FROM trade_records ORDER BY received_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "received_at": r[1],
            "source": r[2],
            "safe_text": r[3],
            "is_publishable": bool(r[4]),
        }
        for r in rows
    ]


@app.get("/api/social-posts")
async def social_posts(limit: int = 20):
    """返回社媒发布任务列表。"""
    try:
        from src.central_brain import get_central_brain
        brain = get_central_brain()
        conn = brain.store._conn()
        rows = conn.execute(
            "SELECT sma_task_id, sma_status, topic, dispatched_at "
            "FROM social_posts ORDER BY dispatched_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        # social_posts 表可能不存在或为空
        return []


_TRADING_AGENT_URL = cfg().get("trading_agent_url")

_analysis_tasks: dict[str, dict[str, Any]] = {}


async def _run_analysis_task(task_id: str, payload: dict) -> None:
    import httpx

    _analysis_tasks[task_id]["status"] = "running"

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(f"{_TRADING_AGENT_URL}/analyze", json=payload)
            resp.raise_for_status()
            result = resp.json()

        _analysis_tasks[task_id].update({
            "status": "done",
            "result": result,
            "elapsed": round(time.time() - _analysis_tasks[task_id]["started_at"], 1),
        })

    except Exception as e:
        _analysis_tasks[task_id].update({
            "status": "error",
            "error": str(e),
            "elapsed": round(time.time() - _analysis_tasks[task_id]["started_at"], 1),
        })


@app.post("/api/stock/analyze")
async def proxy_analyze(request: dict):
    task_id = str(uuid.uuid4())
    symbol = request.get("symbol", "unknown")

    _analysis_tasks[task_id] = {
        "task_id": task_id,
        "symbol": symbol,
        "status": "pending",
        "result": None,
        "error": None,
        "started_at": time.time(),
        "elapsed": 0,
    }

    asyncio.create_task(_run_analysis_task(task_id, request))

    return {"task_id": task_id, "symbol": symbol, "status": "pending"}


@app.get("/api/stock/task/{task_id}")
async def get_task_status(task_id: str):
    task = _analysis_tasks.get(task_id)

    if task is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")

    elapsed = round(time.time() - task["started_at"], 1)
    return {**task, "elapsed": elapsed}


@app.get("/api/stock/report/{symbol}")
async def proxy_report(symbol: str):
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_TRADING_AGENT_URL}/report/{symbol}")
            resp.raise_for_status()
            return resp.json()

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)

    except Exception as e:
        raise HTTPException(status_code=502, detail=f"无法连接 Trading Agent: {e}")


@app.get("/api/dashboard")
async def dashboard_stats():
    """Dashboard 数据聚合：大盘指数、Agent 状态、API 延迟统计。"""
    try:
        brain = _get_central_brain()

        # 1. 大盘指数（从 akshare 获取）
        indices = []
        try:
            import akshare as ak
            # 上证指数
            sh = ak.stock_zh_index_daily(symbol="sh000001")
            sh_latest = sh.iloc[-1]
            indices.append({
                "name": "上证指数",
                "symbol": "SH000001",
                "price": float(sh_latest["close"]),
                "change": float(sh_latest["close"] - sh_latest["open"]),
                "changePercent": float((sh_latest["close"] - sh_latest["open"]) / sh_latest["open"] * 100),
            })
            # 深证成指
            sz = ak.stock_zh_index_daily(symbol="sz399001")
            sz_latest = sz.iloc[-1]
            indices.append({
                "name": "深证成指",
                "symbol": "SZ399001",
                "price": float(sz_latest["close"]),
                "change": float(sz_latest["close"] - sz_latest["open"]),
                "changePercent": float((sz_latest["close"] - sz_latest["open"]) / sz_latest["open"] * 100),
            })
            # 创业板指
            cyb = ak.stock_zh_index_daily(symbol="sz399006")
            cyb_latest = cyb.iloc[-1]
            indices.append({
                "name": "创业板指",
                "symbol": "SZ399006",
                "price": float(cyb_latest["close"]),
                "change": float(cyb_latest["close"] - cyb_latest["open"]),
                "changePercent": float((cyb_latest["close"] - cyb_latest["open"]) / cyb_latest["open"] * 100),
            })
        except Exception as e:
            logger.warning("Failed to fetch market indices: %s", e)
            # 降级到 mock 数据
            indices = [
                {"name": "上证指数", "symbol": "SH000001", "price": 3085.0, "change": 12.5, "changePercent": 0.41},
                {"name": "深证成指", "symbol": "SZ399001", "price": 9876.0, "change": -45.2, "changePercent": -0.46},
                {"name": "创业板指", "symbol": "SZ399006", "price": 1823.0, "change": 8.7, "changePercent": 0.48},
            ]

        # 2. Agent 状态（从 CentralBrain 获取最新日志）
        agents = []
        try:
            recent_logs = brain.store.query_events(limit=20)
            agent_status = {}
            for log in recent_logs:
                agent = log.get("agent", "unknown")
                if agent not in agent_status:
                    agent_status[agent] = {"last_run": log.get("created_at", ""), "status": "running"}
            agents = [
                {"id": "market-brain", "name": "MarketBrain", "type": "MarketBrain", "status": "running", "lastRun": "刚刚", "throughput": "0.8 req/s", "description": "市场情绪与 regime 判断"},
                {"id": "explorer", "name": "Explorer", "type": "DataCollector", "status": "running", "lastRun": "刚刚", "throughput": "1.2 req/s", "description": "市场扫描与热点板块检测"},
                {"id": "strategist", "name": "Strategist", "type": "SignalGenerator", "status": "running", "lastRun": "刚刚", "throughput": "0.5 req/s", "description": "交易信号生成与策略匹配"},
                {"id": "risk-governor", "name": "RiskGovernor", "type": "RiskGovernor", "status": "idle", "lastRun": "5分钟前", "throughput": "0.3 req/s", "description": "风险控制与仓位管理"},
            ]
        except Exception as e:
            logger.warning("Failed to fetch agent status: %s", e)
            agents = []

        # 3. API 延迟统计（从 model_adapter 获取）
        api_stats = {}
        try:
            from src.infra.model_adapter import get_api_stats
            api_stats = get_api_stats()
        except Exception as e:
            logger.warning("Failed to fetch API stats: %s", e)

        # 4. 今日脚本运行次数（从 logs 统计）
        script_runs = 0
        try:
            log_dir = Path("data/logs")
            if log_dir.exists():
                today = datetime.now().strftime("%Y-%m-%d")
                for log_file in log_dir.glob(f"*{today}*.log"):
                    script_runs += 1
        except Exception as e:
            logger.warning("Failed to count script runs: %s", e)

        return {
            "indices": indices,
            "agents": agents,
            "apiStats": api_stats,
            "scriptRuns": script_runs,
        }
    except Exception as e:
        logger.error("Dashboard stats error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))



# ---------------------------------------------------------------------------
# Agent Report API  (S1.6 — Sprint 1)
# ---------------------------------------------------------------------------

def _get_brain():
    """获取 CentralBrain（使用正确的 import 路径）。"""
    from src.central_brain import get_central_brain
    return get_central_brain()


@app.get("/api/agent-report/dates")
async def agent_report_dates():
    """返回有市场判断记录的日期列表（最近 30 天）。"""
    try:
        brain = _get_brain()
        conn = brain.store._conn()
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM market_regime_snapshots "
            "ORDER BY trade_date DESC LIMIT 30"
        ).fetchall()
        return [r["trade_date"] for r in rows]
    except Exception as e:
        logger.error("agent_report_dates error: %s", e)
        return []


@app.get("/api/agent-report/{date}")
async def agent_report(date: str, persona_id: str | None = None):
    """返回某日完整 Agent 报告（regime + picks + signals + risk + orders + attributions）。

    Args:
        persona_id: 可选，按交易人格过滤数据
    """
    try:
        brain = _get_brain()
        store = brain.store
        conn = store._conn()

        # ── 市场判断（全局，不按人格过滤）──
        row = conn.execute(
            "SELECT * FROM market_regime_snapshots "
            "WHERE trade_date = ? ORDER BY created_at DESC LIMIT 1",
            (date,),
        ).fetchone()
        market_regime = None
        if row:
            r = dict(row)
            r["hot_sectors"] = json.loads(r.get("hot_sectors_json") or "[]")
            r["dominant_styles"] = json.loads(r.get("dominant_styles_json") or "[]")
            r["avoid_styles"] = json.loads(r.get("avoid_styles_json") or "[]")
            r["strategy_bias"] = json.loads(r.get("strategy_bias_json") or "{}")
            r["daily_questions"] = json.loads(r.get("daily_questions_json") or "[]")
            r["evidence"] = json.loads(r.get("evidence_json") or "{}")
            market_regime = r

        # ── 选股归档（按人格过滤）──
        picks = store.get_daily_pick(date, persona_id=persona_id)
        if picks:
            picks["hot_sectors"] = json.loads(picks.get("hot_sectors_json") or "[]")
            picks["aggressive"] = json.loads(picks.get("aggressive_json") or "[]")
            picks["stable"] = json.loads(picks.get("stable_json") or "[]")
            picks.setdefault("candidates", [])
            picks.setdefault("candidates_count", 0)
            picks.setdefault("agent_calls_count", 0)
            picks.setdefault("elapsed_seconds", 0)
        else:
            # 如果 daily_picks_archive 没有数据，尝试从 sessions 表获取
            row = conn.execute(
                "SELECT * FROM sessions WHERE created_at LIKE ? ORDER BY created_at DESC LIMIT 1",
                (f"{date}%",),
            ).fetchone()
            if row:
                session = dict(row)
                session_data = json.loads(session.get("data_json") or "{}")
                picks = {
                    "pick_date": date,
                    "hot_sectors": session_data.get("hot_sectors", []),
                    "aggressive": session_data.get("aggressive", []),
                    "stable": session_data.get("stable", []),
                    "candidates": session_data.get("candidates", []),
                    "candidates_count": len(session_data.get("candidates", [])),
                    "agent_calls_count": 0,
                    "elapsed_seconds": 0,
                }

        # ── 交易信号（按人格过滤）──
        if persona_id:
            # 严格过滤：只显示该人格的数据（不显示NULL旧数据）
            rows = conn.execute(
                "SELECT * FROM trade_signals WHERE timestamp LIKE ? AND persona_id = ? ORDER BY timestamp",
                (f"{date}%", persona_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trade_signals WHERE timestamp LIKE ? ORDER BY timestamp",
                (f"{date}%",),
            ).fetchall()
        signals = [dict(r) for r in rows]

        # ── 风控裁决（按人格过滤）──
        if persona_id:
            rows = conn.execute(
                "SELECT * FROM risk_decisions WHERE created_at LIKE ? AND persona_id = ? ORDER BY created_at",
                (f"{date}%", persona_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM risk_decisions WHERE created_at LIKE ? ORDER BY created_at",
                (f"{date}%",),
            ).fetchall()
        risk_decisions = []
        for r in rows:
            d = dict(r)
            d["risk_flags"] = json.loads(d.get("risk_flags_json") or "[]")
            risk_decisions.append(d)

        # ── 订单 + 成交（按人格过滤，通过 fills.persona_id 关联）──
        if persona_id:
            rows = conn.execute(
                """SELECT o.*, f.avg_price, f.fees, f.filled_at, f.persona_id as fill_persona_id
                   FROM orders o
                   LEFT JOIN fills f ON o.order_id = f.order_id
                   WHERE o.submitted_at LIKE ? AND f.persona_id = ?
                   ORDER BY o.submitted_at""",
                (f"{date}%", persona_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT o.*, f.avg_price, f.fees, f.filled_at, f.persona_id as fill_persona_id
                   FROM orders o
                   LEFT JOIN fills f ON o.order_id = f.order_id
                   WHERE o.submitted_at LIKE ? ORDER BY o.submitted_at""",
                (f"{date}%",),
            ).fetchall()
        orders = [dict(r) for r in rows]

        # ── LLM 成本 ──
        row = conn.execute(
            "SELECT COUNT(*) as n, "
            "COALESCE(SUM(total_tokens), 0) as tokens, "
            "COALESCE(SUM(cost_usd), 0.0) as cost "
            "FROM llm_calls WHERE ts LIKE ?",
            (f"{date}%",),
        ).fetchone()
        cost = {
            "total_llm_cost_usd": round(float(row["cost"] or 0), 4),
            "total_calls": int(row["n"] or 0),
            "total_tokens": int(row["tokens"] or 0),
        }

        # ── 交易归因 (S1.6) ──
        rows = conn.execute(
            "SELECT * FROM trade_attributions WHERE created_at LIKE ? ORDER BY created_at DESC",
            (f"{date}%",),
        ).fetchall()
        attributions = []
        for r in rows:
            d = dict(r)
            d["secondary_causes"] = json.loads(d.get("secondary_causes_json") or "[]")
            d["tags"] = json.loads(d.get("tags_json") or "[]")
            attributions.append(d)

        result = {
            "date": date,
            "market_regime": market_regime,
            "picks": picks,
            "signals": signals,
            "risk_decisions": risk_decisions,
            "orders": orders,
            "cost": cost,
            "attributions": attributions,
        }
        # 递归清理特殊浮点值后再序列化
        cleaned_result = clean_special_floats(result)
        json_str = json.dumps(cleaned_result, cls=SafeJSONEncoder)
        return Response(content=json_str, media_type="application/json")

    except Exception as e:
        logger.error("agent_report error for %s: %s", date, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/lessons")
async def get_lessons(
    strategy_id: Optional[str] = None,
    regime: Optional[str] = None,
    limit: int = 10,
):
    """检索最近 lesson 列表，支持按策略和 regime 过滤。"""
    try:
        brain = _get_brain()
        lessons = brain.store.get_recent_lessons(
            strategy_id=strategy_id,
            regime=regime,
            limit=limit,
        )
        for lesson in lessons:
            lesson["tags"] = json.loads(lesson.get("tags_json") or "[]")
        return lessons
    except Exception as e:
        logger.error("get_lessons error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions")
async def get_positions(status: str = "open", persona_id: str | None = None):
    """返回持仓列表。status=open|closed|all，可按人格过滤"""
    try:
        brain = _get_brain()
        conn = brain.store._conn()

        # 构建查询条件
        conditions = []
        params = []

        if status == "open":
            conditions.append("status = 'open'")
        elif status == "closed":
            conditions.append("status = 'closed'")

        if persona_id:
            conditions.append("persona_id = ?")
            params.append(persona_id)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        order_clause = "ORDER BY closed_at DESC LIMIT 50" if status == "closed" else "ORDER BY entry_date DESC LIMIT 100"

        sql = f"SELECT * FROM positions {where_clause} {order_clause}"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_positions error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/fills")
async def get_fills(limit: int = 50, persona_id: str | None = None):
    """返回成交记录列表，可按人格过滤"""
    try:
        brain = _get_brain()
        conn = brain.store._conn()

        if persona_id:
            rows = conn.execute(
                "SELECT * FROM fills WHERE persona_id = ? ORDER BY filled_at DESC LIMIT ?",
                (persona_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM fills ORDER BY filled_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_fills error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/orders")
async def get_orders(limit: int = 50):
    """返回订单列表。"""
    try:
        brain = _get_brain()
        conn = brain.store._conn()
        rows = conn.execute(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_orders error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/portfolio-summary")
async def portfolio_summary(persona_id: str | None = None):
    """返回投资组合汇总统计，可按人格过滤"""
    try:
        brain = _get_brain()
        conn = brain.store._conn()

        # 构建 WHERE 条件
        where_open = "WHERE status = 'open'"
        where_closed = "WHERE status = 'closed'"
        params_open = []
        params_closed = []

        if persona_id:
            where_open += " AND persona_id = ?"
            where_closed += " AND persona_id = ?"
            params_open.append(persona_id)
            params_closed.append(persona_id)

        # 当前持仓统计
        open_rows = conn.execute(
            f"SELECT * FROM positions {where_open}",
            params_open
        ).fetchall()
        open_positions = [dict(r) for r in open_rows]

        total_market_value = 0.0
        total_cost = 0.0
        total_unrealized_pnl = 0.0
        for p in open_positions:
            qty = p.get("current_qty") or 0
            entry = p.get("entry_price") or 0
            current = p.get("current_price") or entry
            mv = qty * current
            cost = qty * entry
            total_market_value += mv
            total_cost += cost
            total_unrealized_pnl += (mv - cost)

        # 已平仓统计
        closed_rows = conn.execute(
            f"SELECT realized_pnl, closed_at FROM positions {where_closed} ORDER BY closed_at",
            params_closed
        ).fetchall()
        total_realized_pnl = sum(r["realized_pnl"] or 0 for r in closed_rows)
        win_count = sum(1 for r in closed_rows if (r["realized_pnl"] or 0) > 0)
        loss_count = sum(1 for r in closed_rows if (r["realized_pnl"] or 0) < 0)
        total_closed = len(closed_rows)
        win_rate = round(win_count / total_closed * 100, 1) if total_closed > 0 else 0

        # 累计收益曲线数据点 (按平仓日期)
        cumulative = 0.0
        pnl_curve = []
        for r in closed_rows:
            cumulative += r["realized_pnl"] or 0
            pnl_curve.append({
                "date": (r["closed_at"] or "")[:10],
                "cumulative_pnl": round(cumulative, 2),
            })

        return {
            "open_count": len(open_positions),
            "closed_count": total_closed,
            "total_market_value": round(total_market_value, 2),
            "total_cost": round(total_cost, 2),
            "total_unrealized_pnl": round(total_unrealized_pnl, 2),
            "total_realized_pnl": round(total_realized_pnl, 2),
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": win_rate,
            "pnl_curve": pnl_curve,
        }
    except Exception as e:
        logger.error("portfolio_summary error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/watchlist")
async def get_watchlist(status: str = "watching"):
    """获取自选股池列表。status: watching / triggered / removed / all"""
    try:
        brain = _get_brain()
        items = brain.store.get_watchlist(status=status)
        return items
    except Exception as e:
        logger.error("get_watchlist error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Strategy Evolution API  (AI 策略进化可视化)
# ---------------------------------------------------------------------------

@app.get("/api/strategy-weights")
async def strategy_weights():
    """返回当前策略权重 + 权重历史。"""
    try:
        weights_path = Path(os.environ.get("DATA_DIR", "data")) / "prompt_weights.json"
        weights: list[dict] = []
        if weights_path.exists():
            with open(weights_path, "r", encoding="utf-8") as f:
                weights = json.load(f)

        # 从 git 或文件时间推断历史（简化：直接返回当前快照）
        return {"weights": weights}
    except Exception as e:
        logger.error("strategy_weights error: %s", e)
        return {"weights": []}


@app.get("/api/personas")
async def get_personas():
    """返回所有交易人格列表。"""
    try:
        from src.agents.cio.trading_persona import list_personas
        personas = list_personas()
        return {"personas": personas}
    except Exception as e:
        logger.error("get_personas error: %s", e)
        return {"personas": []}


@app.get("/api/accounts")
async def get_accounts():
    """返回所有资金账户信息。"""
    try:
        brain = _get_brain()
        accounts = brain.store.list_accounts()
        return {"accounts": accounts}
    except Exception as e:
        logger.error("get_accounts error: %s", e)
        return {"accounts": []}


@app.get("/api/lessons-timeline")
async def lessons_timeline(limit: int = 50):
    """返回 Lessons 时间线（最近 N 条）。"""
    try:
        brain = _get_brain()
        lessons = brain.store.get_recent_lessons(limit=limit)
        for lesson in lessons:
            lesson["tags"] = json.loads(lesson.get("tags_json") or "[]")
        return lessons
    except Exception as e:
        logger.error("lessons_timeline error: %s", e)
        return []


@app.get("/api/attribution-stats")
async def attribution_stats(days: int = 30):
    """返回归因统计。"""
    try:
        brain = _get_brain()
        attrs = brain.store.get_attributions_since(days=days)

        # 按 strategy_id 分组统计
        strategy_stats: dict[str, dict] = {}
        for a in attrs:
            sid = a.get("strategy_id") or "unknown"
            if sid not in strategy_stats:
                strategy_stats[sid] = {"strategy": sid, "win": 0, "loss": 0, "even": 0, "total_pnl": 0.0}
            pnl = a.get("realized_pnl") or 0
            strategy_stats[sid]["total_pnl"] += pnl
            if pnl > 0:
                strategy_stats[sid]["win"] += 1
            elif pnl < 0:
                strategy_stats[sid]["loss"] += 1
            else:
                strategy_stats[sid]["even"] += 1

        # 按 exit_reason 分组
        exit_reasons: dict[str, int] = {}
        for a in attrs:
            reason = a.get("exit_reason") or "unknown"
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        return {
            "attributions": attrs,
            "by_strategy": list(strategy_stats.values()),
            "by_exit_reason": [{"reason": k, "count": v} for k, v in exit_reasons.items()],
        }
    except Exception as e:
        logger.error("attribution_stats error: %s", e)
        return {"attributions": [], "by_strategy": [], "by_exit_reason": []}


@app.get("/api/events/recent")
async def recent_events(limit: int = 20):
    """返回最近的事件记录（用于工作流监控）。"""
    try:
        event_db_path = Path("data/event_bus/events.sqlite")
        if not event_db_path.exists():
            return []

        conn = sqlite3.connect(str(event_db_path))
        rows = conn.execute(
            """
            SELECT id, event_type, payload_json, created_at
            FROM events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()

        events = []
        for r in rows:
            try:
                payload = json.loads(r[2])
            except Exception:
                payload = {}

            events.append({
                "id": r[0],
                "event_type": r[1],
                "created_at": r[3],
                "payload": payload,
            })

        return events
    except Exception as e:
        logger.error("recent_events error: %s", e)
        return []


@app.post("/api/workflow/trigger")
async def trigger_workflow():
    """手动触发完整工作流执行。"""
    try:
        import asyncio
        import subprocess
        import sys
        from pathlib import Path

        # 在后台运行工作流
        def run_workflow():
            try:
                result = subprocess.run(
                    [sys.executable, "-c", "import asyncio; from src.graph.workflow import run_daily_pipeline; asyncio.run(run_daily_pipeline('mock'))"],
                    cwd=Path(__file__).resolve().parents[2],
                    capture_output=True,
                    text=True,
                    timeout=600  # 10分钟超时
                )
                logger.info("Workflow triggered: %s", result.stdout)
                if result.stderr:
                    logger.warning("Workflow stderr: %s", result.stderr)
            except subprocess.TimeoutExpired:
                logger.error("Workflow execution timeout")
            except Exception as e:
                logger.error("Workflow execution failed: %s", e)

        # 在后台线程中运行
        import threading
        thread = threading.Thread(target=run_workflow, daemon=True)
        thread.start()

        return {
            "status": "triggered",
            "message": "工作流已在后台启动，请查看日志监控执行进度"
        }
    except Exception as e:
        logger.error("trigger_workflow error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


_react_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
_static_dir = Path(__file__).resolve().parent / "static"

if _react_dist.exists():

    from fastapi.responses import FileResponse

    @app.get("/")
    def serve_root():
        return FileResponse(str(_react_dist / "index.html"))

    app.mount("/assets", StaticFiles(directory=str(_react_dist / "assets")), name="react-assets")

    @app.get("/{path:path}")
    def serve_spa(path: str):
        file_path = _react_dist / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(_react_dist / "index.html"))

elif _static_dir.exists():

    app.mount("/ui", StaticFiles(directory=str(_static_dir), html=True), name="static")

    @app.get("/")
    def root_redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/ui/")
