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

import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .image_redactor import ImageRedactor
from .text_compliance import ComplianceResult, sanitize_text

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
