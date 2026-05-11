"""收盘分析 — 每日 15:05 汇总当日操作，生成发帖内容。

流程:
1. 从 Central Brain 拉取当日所有持仓 + 复审记录
2. LLM 生成收盘分析（脱敏版）
3. 组装成社媒发帖内容
4. 通过现有 SMA dispatcher 发布（或暂存待发布队列）
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime
from typing import Any

from src.central_brain import get_central_brain
from src.infra.model_adapter import get_llm
from src.stock_analyzer.data_source.intraday_client import IntradayClient
from src.stock_analyzer.data_source.market_summary import summarize_intraday

logger = logging.getLogger(__name__)

CLOSING_SYSTEM_PROMPT = """\
你是「沈经理的AI闭环实验室」的收盘分析撰稿人。
你需要根据今日的持仓表现和交易操作，写一篇小红书风格的收盘总结帖子。

## 要求

1. **脱敏**：不暴露具体代码、名称、股数、总资产。用板块/概念代替具体股票。
2. **风格**：轻松专业，不用过于严肃的金融术语。像是和朋友聊天。
3. **结构**：
   - 一句话总结今日操作（如"今天AI模型让我加仓了科技股，减仓了消费"）
   - 盘中关键决策点的复盘（为什么LLM在某个时点做出了判断）
   - 当日总体盈亏表现（用百分比，不用绝对金额）
   - 明日展望（简短）
   - 风险提示 + hashtag
4. **长度**：300-500字
5. **禁止**：不准出现"必涨""稳赚""翻倍"等违规词

## 输出格式（严格JSON）

```json
{
  "title": "帖子标题（20字以内）",
  "content": "帖子正文",
  "highlights": ["今日亮点1", "今日亮点2"],
  "mood": "bullish|neutral|bearish"
}
```
"""

CLOSING_USER_TEMPLATE = """\
## 今日日期
{today}

## 当前持仓概况
{positions_summary}

## 今日盘中复审记录
{reviews_summary}

## 今日交易操作
{trades_summary}

## 整体表现
{performance_summary}

请根据以上信息撰写今日收盘分析帖子。
"""


async def run_closing_analysis(
    model_name: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """执行收盘分析并生成发帖内容。

    Args:
        model_name: 指定LLM模型
        dry_run: 只生成不发布

    Returns:
        {"post": {...}, "dispatched": bool}
    """
    brain = get_central_brain()
    today = date.today().isoformat()

    # 1. Gather data
    positions = brain.store.list_open_positions()
    closed_today = _get_closed_today(brain, today)
    all_positions = positions + closed_today

    if not all_positions:
        logger.info("无持仓记录，跳过收盘分析")
        return {"post": None, "dispatched": False}

    # 2. Gather reviews from today
    reviews_today = _gather_today_reviews(brain, all_positions, today)

    # 3. Build summaries
    positions_summary = _build_positions_summary(positions)
    reviews_summary = _build_reviews_summary(reviews_today)
    trades_summary = _build_trades_summary(reviews_today, closed_today)
    performance_summary = _build_performance_summary(all_positions)

    user_msg = CLOSING_USER_TEMPLATE.format(
        today=today,
        positions_summary=positions_summary,
        reviews_summary=reviews_summary,
        trades_summary=trades_summary,
        performance_summary=performance_summary,
    )

    # 4. Call LLM
    llm = get_llm(model_name=model_name)
    messages = [
        {"role": "system", "content": CLOSING_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    try:
        response = llm.invoke(messages)
        post_data = _parse_closing_response(response.content)
    except Exception as e:
        logger.error("收盘分析 LLM 调用失败: %s", e)
        post_data = _fallback_post(all_positions, reviews_today)

    # 5. Persist & optionally dispatch
    post_id = f"CLOSE-{today}-{uuid.uuid4().hex[:6].upper()}"
    post_data["post_id"] = post_id
    post_data["date"] = today
    post_data["position_count"] = len(positions)
    post_data["trade_count"] = len([r for r in reviews_today if r.get("action") != "HOLD"])

    brain.log_agent_event(
        session_id=f"closing-{today}",
        agent="closing_analyst",
        event_type="closing_analysis_generated",
        payload=post_data,
    )

    dispatched = False
    if not dry_run:
        try:
            dispatched = _dispatch_to_sma(brain, post_data, today)
        except Exception as e:
            logger.warning("SMA dispatch 失败: %s", e)

    logger.info(
        "收盘分析完成: %d 只持仓, %d 个操作, dispatched=%s",
        len(positions), post_data["trade_count"], dispatched,
    )
    return {"post": post_data, "dispatched": dispatched}


# ------------------------------------------------------------------
# Data gathering helpers
# ------------------------------------------------------------------

def _get_closed_today(brain, today: str) -> list[dict]:
    conn = brain.store._conn()
    rows = conn.execute(
        "SELECT * FROM positions WHERE status = 'closed' AND closed_at LIKE ?",
        (f"{today}%",),
    ).fetchall()
    return [dict(r) for r in rows]


def _gather_today_reviews(brain, positions: list[dict], today: str) -> list[dict]:
    all_reviews = []
    for pos in positions:
        reviews = brain.store.list_position_reviews(pos["position_id"], limit=20)
        for r in reviews:
            if r.get("review_at", "").startswith(today):
                r["symbol"] = pos.get("symbol", "")
                r["name"] = pos.get("name", "")
                all_reviews.append(r)
    return all_reviews


def _build_positions_summary(positions: list[dict]) -> str:
    if not positions:
        return "当前无持仓"
    lines = []
    for p in positions:
        lines.append(
            f"- {p.get('name', p['symbol'])} | "
            f"成本 {p.get('entry_price', '?')} | "
            f"数量 {p.get('current_qty', '?')} | "
            f"策略: {p.get('original_strategy', '未知')}"
        )
    return "\n".join(lines)


def _build_reviews_summary(reviews: list[dict]) -> str:
    if not reviews:
        return "今日无复审记录"
    lines = []
    for r in reviews:
        ts = r.get("review_at", "")[-8:-3] if r.get("review_at") else "?"
        lines.append(
            f"- {ts} | {r.get('symbol', '?')} | {r['action']} | "
            f"价格 {r.get('current_price', '?')} | 盈亏 {r.get('pnl_pct', '?')}% | "
            f"{r.get('reason', '')[:50]}"
        )
    return "\n".join(lines)


def _build_trades_summary(reviews: list[dict], closed: list[dict]) -> str:
    trades = [r for r in reviews if r.get("action") not in ("HOLD", None)]
    if not trades and not closed:
        return "今日无交易操作（全部HOLD）"
    lines = []
    for t in trades:
        lines.append(f"- {t.get('symbol', '?')}: {t['action']} ({t.get('reason', '')[:40]})")
    for c in closed:
        pnl = c.get("realized_pnl", 0)
        lines.append(f"- {c.get('name', c['symbol'])}: 清仓, PnL={pnl:+.2f}")
    return "\n".join(lines)


def _build_performance_summary(positions: list[dict]) -> str:
    open_count = sum(1 for p in positions if p.get("status") == "open")
    closed_count = sum(1 for p in positions if p.get("status") == "closed")
    total_pnl = sum(p.get("realized_pnl", 0) or 0 for p in positions if p.get("status") == "closed")
    return (
        f"持仓中: {open_count} 只 | 今日平仓: {closed_count} 只 | "
        f"已实现盈亏: {total_pnl:+.2f}"
    )


# ------------------------------------------------------------------
# LLM response parsing
# ------------------------------------------------------------------

def _parse_closing_response(content: str) -> dict:
    text = content.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "title": "AI交易日记 | 今日复盘",
            "content": content[:500],
            "highlights": [],
            "mood": "neutral",
        }


def _fallback_post(positions: list[dict], reviews: list[dict]) -> dict:
    actions = [r for r in reviews if r.get("action") != "HOLD"]
    return {
        "title": f"AI交易日记 | 今日{len(actions)}个操作",
        "content": (
            f"今日AI模型监控了{len(positions)}只持仓，"
            f"产生了{len(actions)}个交易信号。\n\n"
            f"⚠️ 此为AI实验记录，不构成投资建议。\n\n"
            f"#AI交易 #量化投资 #沈经理实盘"
        ),
        "highlights": [],
        "mood": "neutral",
    }


# ------------------------------------------------------------------
# SMA dispatch
# ------------------------------------------------------------------

def _dispatch_to_sma(brain, post_data: dict, today: str) -> bool:
    """尝试通过现有 SMA dispatcher 发布。"""
    try:
        from src.social_media_dispatcher.client import SmaClient
        from src.social_media_dispatcher.topic_router import TopicPayload

        client = SmaClient()
        payload = TopicPayload(
            account_id="XHS_01",
            topic=post_data.get("title", "AI交易日记"),
            body=post_data.get("content", ""),
            images=[],
        )
        result = client.dispatch(payload)
        if result and getattr(result, "success", False):
            brain.store.record_social_post(
                sma_task_id=getattr(result, "task_id", post_data["post_id"]),
                account_id="XHS_01",
                platform="xiaohongshu",
                source_pick_date=today,
                topic=post_data.get("title", ""),
            )
            return True
    except ImportError:
        logger.debug("SMA dispatcher 未配置，跳过发布")
    except Exception as e:
        logger.warning("SMA dispatch error: %s", e)
    return False
