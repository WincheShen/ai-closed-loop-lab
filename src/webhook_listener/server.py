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
_conn = sqlite3.connect(_DB_PATH, check_same_thread=False, isolation_level=None)
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
