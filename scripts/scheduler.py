#!/usr/bin/env python3
"""定时调度脚本 — 基于 schedule 库的日常任务调度。

部署建议：
    - 开发调试：直接运行 python scripts/scheduler.py
    - 生产部署：systemd 服务或 Docker 容器后台运行
    - 也可改用 APScheduler/Celery 替代

调度表：
    - 9:35: 开盘后 MarketBrain 第一次判定 (intraday regime snapshot)
    - 盘中 9:30-15:00 每 30 分钟: 持仓复审 (intraday_review)
    - 11:35: 午盘后 MarketBrain 重判定
    - 14:00: 尾盘前 MarketBrain 重判定
    - 15:05: 收盘分析 + 发帖 (closing_analysis)
    - 15:35: 完整 LangGraph 管线 (MarketBrain→...→Executioner→Influencer)
    - 16:00: 健康检查 (确认各节点今日是否产出数据)
    - 每周日 20:00: 周复盘 (weekly_feedback)
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging
import time

import schedule

from src.agents.cio.market_brain import MarketBrain
from src.agents.cio.trading_persona import get_persona
from src.agents.reviewer.intraday_loop import run_intraday_review
from src.graph.workflow import run_daily_pipeline, run_weekly_feedback
from src.infra.logger import setup_logging

logger = logging.getLogger(__name__)


# --- 定时任务 ---

def job_intraday_review() -> None:
    """盘中每 30 分钟持仓复审。"""
    logger.info("⏰ 定时任务触发: 盘中复审")
    results = asyncio.run(run_intraday_review())
    actions = [r for r in results if r.get("action") != "HOLD"]
    if actions:
        logger.info("复审产生 %d 个交易动作", len(actions))


def job_market_brain_only() -> None:
    """盘中 MarketBrain 单独判定（不触发交易），用于积累 regime 漂移数据。"""
    logger.info("⏰ 定时任务触发: MarketBrain 单独判定")
    session_id = f"mb-{datetime.now().strftime('%Y%m%d-%H%M')}-{uuid.uuid4().hex[:4]}"
    try:
        brain = MarketBrain(session_id, persona=get_persona())
        snap = brain.generate_snapshot()
        logger.info(
            "MarketBrain 盘中判定: regime=%s posture=%s max_pos=%.0f%% hot=%s",
            snap.regime,
            snap.recommended_posture,
            snap.max_total_position_pct * 100,
            ",".join(snap.hot_sectors[:3]) or "无",
        )
    except Exception as e:
        logger.warning("MarketBrain 盘中判定失败: %s", e)


def job_closing_analysis() -> None:
    """每日 15:05 收盘分析 + 生成发帖内容。"""
    logger.info("⏰ 定时任务触发: 收盘分析")
    try:
        from src.agents.reviewer.closing_analysis import run_closing_analysis
        asyncio.run(run_closing_analysis())
    except ImportError:
        logger.warning("closing_analysis 模块尚未实现，跳过")


def job_daily_pipeline() -> None:
    """每日 15:35 收盘后完整管线 (MarketBrain→Explorer→Strategist→RiskGovernor→Executioner→Influencer)。"""
    logger.info("⏰ 定时任务触发: 每日完整管线 (mock)")
    asyncio.run(run_daily_pipeline("mock"))


def job_health_check() -> None:
    """每日 16:00 健康检查 — 确认各节点今日是否产出数据。"""
    from src.central_brain import get_central_brain
    from datetime import date as _date

    today = _date.today().isoformat()
    brain = get_central_brain()
    conn = brain.store._conn()

    checks = {}

    # MarketBrain 是否产出 regime
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM market_regime_snapshots WHERE trade_date = ?", (today,)
    ).fetchone()
    checks["market_regime"] = row["cnt"] > 0

    # 是否有 session
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM sessions WHERE created_at LIKE ?", (f"{today}%",)
    ).fetchone()
    checks["pipeline_ran"] = row["cnt"] > 0

    # trade_signals (0 也可以，只是记录)
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM trade_signals WHERE timestamp LIKE ?", (f"{today}%",)
    ).fetchone()
    checks["signals_count"] = row["cnt"]

    # risk_decisions
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM risk_decisions WHERE created_at LIKE ?", (f"{today}%",)
    ).fetchone()
    checks["risk_decisions_count"] = row["cnt"]

    # 报告
    all_ok = checks["market_regime"] and checks["pipeline_ran"]
    for name, val in checks.items():
        status = "OK" if val else "MISSING"
        if isinstance(val, int):
            status = str(val)
        logger.info("[HealthCheck] %s: %s", name, status)

    if not all_ok:
        logger.warning("[HealthCheck] ⚠️ 今日有节点未产出数据！检查 scheduler 日志")


def job_weekly_feedback() -> None:
    """每周日 20:00 复盘。"""
    logger.info("⏰ 定时任务触发: 周复盘")
    asyncio.run(run_weekly_feedback())


def job_backfill_attributions() -> None:
    """每日 15:15 补跑缺失归因 — 保底机制。

    对所有 status='closed' 但无 trade_attributions 记录的 position 补跑归因。
    """
    logger.info("⏰ 定时任务触发: 归因补跑")
    try:
        from src.agents.memory.trade_attribution import TradeAttributor
        from src.central_brain import get_central_brain

        brain = get_central_brain()
        conn = brain.store._conn()
        rows = conn.execute(
            """SELECT p.* FROM positions p
            LEFT JOIN trade_attributions ta ON p.position_id = ta.position_id
            WHERE p.status = 'closed' AND ta.attribution_id IS NULL""",
        ).fetchall()

        if not rows:
            logger.info("所有已平仓 position 均已归因")
            return

        attributor = TradeAttributor("scheduler-backfill")
        ok = 0
        for row in rows:
            position = dict(row)
            try:
                attributor.attribute_and_save(position, close_price=position.get("close_price"))
                ok += 1
            except Exception as e:
                logger.warning("归因补跑失败 %s: %s", position.get("symbol"), e)

        logger.info("归因补跑完成: %d/%d 成功", ok, len(rows))
    except Exception as e:
        logger.warning("归因补跑任务异常: %s", e)


# --- 调度配置 ---

def setup_schedule() -> None:
    """配置所有定时任务。"""
    # 盘中每 30 分钟持仓复审 (9:30 - 14:30)
    for hour in range(9, 15):
        for minute in (0, 30):
            if hour == 9 and minute == 0:
                continue
            if hour >= 12 and hour < 13:
                continue
            if hour == 14 and minute == 30:
                continue
            t = f"{hour:02d}:{minute:02d}"
            schedule.every().day.at(t).do(job_intraday_review)

    schedule.every().day.at("09:35").do(job_market_brain_only)
    schedule.every().day.at("11:35").do(job_market_brain_only)
    schedule.every().day.at("14:00").do(job_market_brain_only)

    schedule.every().day.at("15:05").do(job_closing_analysis)
    schedule.every().day.at("15:15").do(job_backfill_attributions)
    schedule.every().day.at("15:35").do(job_daily_pipeline)
    schedule.every().day.at("16:00").do(job_health_check)

    schedule.every().sunday.at("20:00").do(job_weekly_feedback)

    logger.info("调度器已启动 — 当前时间: %s", datetime.now().isoformat())

    for job in schedule.get_jobs():
        logger.info("任务注册: %s | 下次运行: %s", job, job.next_run)


def run_scheduler() -> None:
    """主循环。"""
    setup_schedule()

    last_heartbeat = 0

    while True:
        schedule.run_pending()

        now = time.time()
        if now - last_heartbeat > 300:
            logger.info("scheduler heartbeat — alive")
            last_heartbeat = now

        time.sleep(30)


if __name__ == "__main__":
    setup_logging()
    run_scheduler()
