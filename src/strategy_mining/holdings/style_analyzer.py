"""M6: Analyze inferred trades + trader philosophy to characterize trading style.

Output is a structured JSON with:
- style_tags: list[str]
- avg_holding_days: float
- win_rate: float
- profit_factor: float
- max_single_loss_pct: float
- max_single_win_pct: float
- sector_tilt: dict[str, float]
- position_sizing_pattern: str
- risk_management_notes: str
- alignment_with_philosophy: str
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from strategy_mining.db import connection_scope
from infra.model_adapter import get_deep_think_llm
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)

SYSTEM = """你是一名资深的量化交易风格分析师。
请根据以下【交易记录】和【交易员自述哲学】，对该交易员的交易风格进行结构化分析。

分析维度：
1. style_tags: 标签列表（如"趋势跟踪"、"波段操作"、"高集中度"、"截断亏损让利润奔跑"等）
2. avg_holding_days: 平均持仓天数（仅统计有 realized_pnl 的卖出/减仓事件）
3. win_rate: 盈利交易占比
4. profit_factor: 总盈利/总亏损绝对值
5. max_single_loss_pct: 单笔最大亏损占该笔名义本金的百分比
6. max_single_win_pct: 单笔最大盈利占该笔名义本金的百分比
7. sector_tilt: 板块偏好（统计买入/加仓的票所属申万一级行业，按次数加权）
8. position_sizing_pattern: 仓位管理特征描述
9. risk_management_notes: 止损/止盈特征
10. alignment_with_philosophy: 实际交易行为与自述哲学的吻合度及偏差

输出必须是严格的 JSON，不要 Markdown 代码块包装。"""


def _fetch_trades(trader_alias: str) -> list[dict[str, Any]]:
    sql = """
        SELECT trade_date, event_type, symbol, stock_name_full,
               delta_qty, trade_price_estimate, realized_pnl,
               holding_days_at_close, confidence, notes
          FROM strategy_mining.holding_trades
         WHERE trader_alias = %s
         ORDER BY trade_date;
    """
    with connection_scope(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, (trader_alias,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _fetch_philosophy(trader_alias: str) -> str | None:
    sql = "SELECT memoir_texts FROM strategy_mining.trader_profile WHERE trader_alias = %s"
    with connection_scope(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, (trader_alias,))
        row = cur.fetchone()
        if row and row[0]:
            texts = row[0]
            if isinstance(texts, list):
                return "\n---\n".join(texts)
            return str(texts)
    return None


def _build_prompt(trades: list[dict], philosophy: str | None) -> str:
    lines = ["【交易记录】"]
    for t in trades:
        sign = "+" if (t.get("realized_pnl") or 0) > 0 else ""
        lines.append(
            f"{t['trade_date']}  {t['event_type']:12s}  {t['symbol']:8s}  "
            f"Δ={t['delta_qty']:>6}  px={t['trade_price_estimate']}  "
            f"pnl={sign}{t.get('realized_pnl')}  conf={t['confidence']}"
        )
    if philosophy:
        lines.append("\n【交易员自述哲学】\n" + philosophy)
    else:
        lines.append("\n【交易员自述哲学】\n（未提供）")
    return "\n".join(lines)


@dataclass
class StyleReport:
    trader_alias: str
    style_tags: list[str]
    avg_holding_days: float | None
    win_rate: float | None
    profit_factor: float | None
    max_single_loss_pct: float | None
    max_single_win_pct: float | None
    sector_tilt: dict[str, float]
    position_sizing_pattern: str
    risk_management_notes: str
    alignment_with_philosophy: str
    raw_json: dict[str, Any]


def analyze(trader_alias: str, philosophy_override: str | None = None) -> StyleReport:
    trades = _fetch_trades(trader_alias)
    if not trades:
        raise ValueError(f"No trades found for {trader_alias}")

    phil = philosophy_override or _fetch_philosophy(trader_alias) or "（未提供交易员自述哲学）"
    prompt = _build_prompt(trades, phil)

    llm = get_deep_think_llm(model_name="gpt-5.3-chat")
    resp = llm.invoke(
        [SystemMessage(content=SYSTEM), HumanMessage(content=prompt)]
    )
    content = resp.content.strip()
    # Try to extract JSON even if wrapped in markdown
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.startswith("json"):
            content = content[4:].strip()
    data = json.loads(content)

    return StyleReport(
        trader_alias=trader_alias,
        style_tags=data.get("style_tags", []),
        avg_holding_days=data.get("avg_holding_days"),
        win_rate=data.get("win_rate"),
        profit_factor=data.get("profit_factor"),
        max_single_loss_pct=data.get("max_single_loss_pct"),
        max_single_win_pct=data.get("max_single_win_pct"),
        sector_tilt=data.get("sector_tilt", {}),
        position_sizing_pattern=data.get("position_sizing_pattern", ""),
        risk_management_notes=data.get("risk_management_notes", ""),
        alignment_with_philosophy=data.get("alignment_with_philosophy", ""),
        raw_json=data,
    )


