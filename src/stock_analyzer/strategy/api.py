"""策略管理 API — FastAPI Router。

端点：
    POST /api/strategy/compile    — 自然语言 → StrategySpec
    POST /api/strategy/execute    — StrategySpec → 选股结果
    GET  /api/strategy/list       — 已保存策略列表
    POST /api/strategy/save       — 保存策略到数据库
    DELETE /api/strategy/{id}     — 删除策略
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .compiler import StrategyCompiler, StrategySpec
from .executor import StrategyExecutor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategy", tags=["strategy"])

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CompileRequest(BaseModel):
    strategy_text: str
    use_mock_llm: bool = False  # True 则不调 LLM，用关键词匹配


class CompileResponse(BaseModel):
    success: bool
    spec: dict
    error: Optional[str] = None


class ExecuteRequest(BaseModel):
    spec: dict  # StrategySpec.to_dict() 的结果
    use_mock_data: bool = False  # True 则用 mock 行情


class ExecuteResponse(BaseModel):
    success: bool
    strategy_name: str
    is_mock_data: bool
    total_stocks: int
    filtered_count: int
    picks: list[dict]
    error: Optional[str] = None


class SaveRequest(BaseModel):
    strategy_text: str
    spec: dict
    name: Optional[str] = None


class StrategyListItem(BaseModel):
    id: str
    name: str
    description: str
    created_at: str
    filters_count: int
    rankings_count: int


# ---------------------------------------------------------------------------
# DB helper (SQLite)
# ---------------------------------------------------------------------------

_DB_PATH: Optional[Path] = None


def _get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = Path(__file__).resolve().parents[3] / "data" / "strategies.db"
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _DB_PATH


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_get_db_path()))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS strategies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            strategy_text TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/compile", response_model=CompileResponse)
def compile_strategy(req: CompileRequest):
    """将自然语言策略编译为结构化规则（供 Review）。"""
    if not req.strategy_text.strip():
        raise HTTPException(400, "strategy_text 不能为空")

    try:
        compiler = StrategyCompiler()
        if req.use_mock_llm:
            spec = compiler.compile_mock(req.strategy_text)
        else:
            spec = compiler.compile(req.strategy_text)

        return CompileResponse(success=True, spec=spec.to_dict())
    except Exception as e:
        logger.exception("Compile failed: %s", e)
        return CompileResponse(success=False, spec={}, error=str(e))


@router.post("/execute", response_model=ExecuteResponse)
def execute_strategy(req: ExecuteRequest):
    """根据确认后的 StrategySpec 执行选股。"""
    try:
        spec = StrategySpec.from_dict(req.spec)
        executor = StrategyExecutor(allow_mock=True)

        # 获取快照（use_mock_data=True 时直接用 mock，跳过网络请求）
        if req.use_mock_data:
            snapshot = executor.akshare_client._fetch_mock()
        else:
            snapshot = executor.akshare_client.fetch_snapshot()

        # 执行
        picks = executor.execute(spec, snapshot=snapshot)

        return ExecuteResponse(
            success=True,
            strategy_name=spec.name,
            is_mock_data=snapshot.is_mock,
            total_stocks=len(snapshot.stocks),
            filtered_count=len(picks),
            picks=[p.to_dict() for p in picks],
        )
    except Exception as e:
        logger.exception("Execute failed: %s", e)
        return ExecuteResponse(
            success=False,
            strategy_name="",
            is_mock_data=False,
            total_stocks=0,
            filtered_count=0,
            picks=[],
            error=str(e),
        )


@router.get("/list", response_model=list[StrategyListItem])
def list_strategies():
    """列出所有已保存的策略。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, name, strategy_text, spec_json, created_at FROM strategies ORDER BY created_at DESC"
    ).fetchall()
    conn.close()

    items = []
    for row in rows:
        spec_dict = json.loads(row[3])
        items.append(StrategyListItem(
            id=row[0],
            name=row[1],
            description=row[2][:100],
            created_at=row[4],
            filters_count=len(spec_dict.get("filters", [])),
            rankings_count=len(spec_dict.get("rankings", [])),
        ))
    return items


@router.post("/save")
def save_strategy(req: SaveRequest):
    """保存策略到数据库。"""
    strategy_id = uuid.uuid4().hex[:12]
    name = req.name or req.spec.get("name", "未命名策略")
    now = datetime.now().isoformat()

    conn = _get_conn()
    conn.execute(
        "INSERT INTO strategies (id, name, strategy_text, spec_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (strategy_id, name, req.strategy_text, json.dumps(req.spec, ensure_ascii=False), now),
    )
    conn.commit()
    conn.close()

    return {"success": True, "id": strategy_id, "name": name}


@router.delete("/{strategy_id}")
def delete_strategy(strategy_id: str):
    """删除策略。"""
    conn = _get_conn()
    conn.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
    conn.commit()
    conn.close()
    return {"success": True, "id": strategy_id}


@router.get("/{strategy_id}")
def get_strategy(strategy_id: str):
    """获取单个策略详情。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, name, strategy_text, spec_json, created_at FROM strategies WHERE id = ?",
        (strategy_id,),
    ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(404, "Strategy not found")

    return {
        "id": row[0],
        "name": row[1],
        "strategy_text": row[2],
        "spec": json.loads(row[3]),
        "created_at": row[4],
    }
