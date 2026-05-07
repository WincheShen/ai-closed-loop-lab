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
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
_conn = sqlite3.connect(_DB_PATH, check_same_thread=False, isolation_level=None)
_conn.executescript("""
CREATE TABLE IF NOT EXISTS trade_records (
    id              TEXT PRIMARY KEY,
    received_at     TEXT NOT NULL,
    source          TEXT NOT NULL,        -- wechat | feishu | manual
    raw_text        TEXT,
    safe_text       TEXT,
    forbidden_hits  TEXT,                  -- JSON
    is_publishable  INTEGER NOT NULL,
    raw_image_path  TEXT,
    redacted_image_path TEXT,
    metadata_json   TEXT
);
""")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Lab Webhook Listener",
    description="接收 wechat/飞书 推送的交易记录并做合规预处理",
    version="0.1.0",
)
redactor = ImageRedactor()

# 注册策略管理 API Router
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
    """通用交易记录接收端点。

    支持：
    - 纯文字（沈经理在 wechat 群发）
    - 文字 + 图片（持仓截图）
    """
    if not text and not image:
        raise HTTPException(status_code=400, detail="text and image cannot both be empty")

    record_id = uuid.uuid4().hex[:12]
    received_at = datetime.now()

    # 1. 文字合规
    compliance: ComplianceResult = sanitize_text(text)

    # 2. 图片落盘 + 脱敏
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
        except Exception as e:  # noqa: BLE001
            logger.exception("image redact failed for %s", record_id)
            redacted_image_path = None

    # 3. 落库
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

    # 4. 发布事件到 Central Brain EventBus（Phase 4）
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from ai_platform.central_brain.event_bus.event_bus import get_event_bus

        event_bus = get_event_bus()

        event_bus.publish(
            "trade.record.created",
            {
                "record_id": record_id,
                "received_at": received_at.isoformat(),
                "source": source,
                "safe_text": compliance.safe_text,
                "is_publishable": compliance.is_publishable,
                "redacted_image_path": str(redacted_image_path) if redacted_image_path else None,
            },
        )
    except Exception as e:
        logger.warning("failed to publish trade.record.created event: %s", e)

    # 5. Phase 3: 自动触发 SMA dispatch（如果启用且可发布）
    if _auto_dispatch_enabled and compliance.is_publishable:
        try:
            router = _get_topic_router()
            client = _get_sma_client()
            brain = _get_central_brain()

            # 组装 TopicPayload
            payload = router.from_trade_record(
                record_id=record_id,
                safe_text=compliance.safe_text,
                received_at=received_at,
                account_id=_sma_default_account,
                redacted_image_url=str(redacted_image_path) if redacted_image_path else None,
            )

            # dispatch 到 SMA
            result = client.dispatch(payload)

            if result.success:
                # 记录到 social_posts 表（用于后续互动同步）
                brain.store.record_social_post(
                    sma_task_id=result.sma_task_id or record_id,
                    account_id=_sma_default_account,
                    platform="xhs",  # 默认小红书
                    source_pick_date=None,  # trade_record 没有对应选股日期
                    source_symbols=[],
                    topic=compliance.safe_text[:100],
                    dispatched_at=received_at.isoformat(),
                )
                logger.info(
                    "Auto-dispatched to SMA: record_id=%s sma_task_id=%s",
                    record_id, result.sma_task_id
                )
            else:
                logger.warning(
                    "Auto-dispatch to SMA failed: record_id=%s error=%s",
                    record_id, result.error
                )
        except Exception as e:
            logger.exception("Auto-dispatch to SMA failed for record %s: %s", record_id, e)

    logger.info(
        "trade record received id=%s source=%s publishable=%s replacements=%d",
        record_id, source, compliance.is_publishable, compliance.replacements_applied,
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
def recent_records(limit: int = 20) -> list[dict]:
    rows = _conn.execute(
        "SELECT id, received_at, source, safe_text, is_publishable "
        "FROM trade_records ORDER BY received_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "id": r[0], "received_at": r[1], "source": r[2],
            "safe_text": r[3], "is_publishable": bool(r[4]),
        }
        for r in rows
    ]


@app.get("/api/social-posts")
def list_social_posts(limit: int = 20) -> list[dict]:
    """返回社媒发布任务列表（用于管理页面展示）。"""
    try:
        brain = _get_central_brain()
        posts = brain.store.list_social_posts(limit=limit)
        return posts
    except Exception as e:
        logger.error("Failed to list social posts: %s", e)
        return []


# ---------------------------------------------------------------------------
# Stock Analysis Proxy（异步任务队列，转发到 Trading Agent Service）
# ---------------------------------------------------------------------------
_TRADING_AGENT_URL = os.environ.get("TRADING_AGENT_URL", "http://localhost:8010")

# task_id -> {status, result, error, symbol, started_at, elapsed}
_analysis_tasks: dict[str, dict[str, Any]] = {}


async def _run_analysis_task(task_id: str, payload: dict) -> None:
    """后台异步执行分析，结果写回 _analysis_tasks。"""
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
    """提交个股分析任务，立即返回 task_id，前端轮询 /api/stock/task/{task_id}。"""
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
    """查询分析任务状态。

    status 取值：
      - pending  — 任务已提交，排队中
      - running  — 正在分析
      - done     — 完成，result 字段包含报告
      - error    — 出错，error 字段包含原因
    """
    task = _analysis_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    elapsed = round(time.time() - task["started_at"], 1)
    return {**task, "elapsed": elapsed}


@app.get("/api/stock/report/{symbol}")
async def proxy_report(symbol: str):
    """代理获取缓存报告（不触发新分析）。"""
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


# 挂载静态文件（管理页面）- 挂载到 /ui 路径
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(_static_dir), html=True), name="static")


@app.get("/")
def root_redirect():
    """根路径重定向到管理页面。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/ui/")
