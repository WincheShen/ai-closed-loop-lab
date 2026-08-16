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
import os
import signal as signal_mod
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging
import time

import schedule

from src.agents.cio.market_brain import MarketBrain
from src.agents.cio.trading_persona import get_persona, list_personas
from src.agents.reviewer.intraday_loop import run_intraday_review
from src.agents.reviewer.stale_position_check import check_stale_positions
from src.graph.workflow import run_daily_pipeline, run_weekly_feedback
from src.infra.logger import setup_logging

logger = logging.getLogger(__name__)


# --- 交易日检查 ---

def is_trading_day() -> bool:
    """检查今天是否是交易日（非周末）。"""
    weekday = datetime.now().weekday()
    return weekday < 5  # 0-4 是周一到周五


def skip_if_weekend(func):
    """装饰器：周末跳过执行。"""
    def wrapper(*args, **kwargs):
        if not is_trading_day():
            logger.info("今天是周末，跳过 %s", func.__name__)
            return None
        return func(*args, **kwargs)
    return wrapper


# --- 定时任务 ---

@skip_if_weekend
def job_intraday_review() -> None:
    """盘中每 30 分钟持仓复审 — 为所有活跃人格分别执行。"""
    logger.info("⏰ 定时任务触发: 盘中复审 (多人格模式)")

    personas = list_personas()
    if not personas:
        logger.warning("未找到任何人格配置，使用默认人格复审")
        results = asyncio.run(run_intraday_review())
        actions = [r for r in results if r.get("action") != "HOLD"]
        if actions:
            logger.info("复审产生 %d 个交易动作", len(actions))
        return

    total_actions = 0
    for p in personas:
        persona_id = p["id"]
        persona_name = p.get("name", persona_id)
        logger.info("开始复审人格: %s (%s)", persona_name, persona_id)
        try:
            results = asyncio.run(run_intraday_review(persona_id=persona_id))
            actions = [r for r in results if r.get("action") != "HOLD"]
            if actions:
                logger.info("人格 %s 复审产生 %d 个交易动作", persona_name, len(actions))
                total_actions += len(actions)
            else:
                logger.info("人格 %s 无交易动作", persona_name)
        except Exception as e:
            logger.error("人格 %s 复审失败: %s", persona_name, e)

    if total_actions > 0:
        logger.info("总计产生 %d 个交易动作", total_actions)


@skip_if_weekend
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


@skip_if_weekend
def job_regime_drift_check() -> None:
    """盘中每 5 分钟 regime drift 快速检测（纯量化，无 LLM）。"""
    try:
        from src.agents.cio.regime_drift import detect_regime_drift
        result = detect_regime_drift()
        if result.get("drifted"):
            logger.warning(
                "⚠️ REGIME DRIFT: %s → %s (severity=%d) | action=%s",
                result["from_regime"], result["to_regime"],
                result["severity"], result.get("action_taken"),
            )
        elif result.get("severity", 0) > 0:
            logger.info(
                "Drift 信号 (待确认): %s → %s (severity=%d)",
                result.get("from_regime"), result.get("to_regime"),
                result.get("severity"),
            )
    except Exception as e:
        logger.debug("Regime drift 检测异常: %s", e)


@skip_if_weekend
def job_closing_analysis() -> None:
    """每日 15:05 收盘分析 + 生成发帖内容 + 自动发布到社交平台。"""
    logger.info("⏰ 定时任务触发: 收盘分析")
    try:
        from src.agents.reviewer.closing_analysis import run_closing_analysis
        result = asyncio.run(run_closing_analysis(dry_run=False))
        dispatched = result.get("dispatched", False)
        if dispatched:
            logger.info("✅ 收盘分析已发布到社交平台")
        else:
            logger.info("收盘分析已生成，未发布（检查 social_accounts.yaml 配置）")
    except ImportError:
        logger.warning("closing_analysis 模块尚未实现，跳过")
    except Exception as e:
        logger.error("收盘分析异常: %s", e)


@skip_if_weekend
def job_daily_pipeline() -> None:
    """每日 15:35 收盘后完整管线 — 为所有活跃人格分别执行。"""
    logger.info("⏰ 定时任务触发: 每日完整管线 (多人格模式)")

    personas = list_personas()
    if not personas:
        logger.warning("未找到任何人格配置，使用默认人格运行")
        asyncio.run(run_daily_pipeline("mock"))
        return

    logger.info("为 %d 个人格执行每日交易管线", len(personas))

    for p in personas:
        persona_id = p["id"]
        persona_name = p.get("name", persona_id)
        logger.info("开始执行人格: %s (%s)", persona_name, persona_id)
        try:
            asyncio.run(run_daily_pipeline("mock", persona_id=persona_id))
            logger.info("人格 %s 执行完成", persona_name)
        except Exception as e:
            logger.error("人格 %s 执行失败: %s", persona_name, e)


@skip_if_weekend
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

    # AKShare 数据源健康
    try:
        from src.stock_analyzer.data_source.akshare_client import get_akshare_health
        ak_health = get_akshare_health()
        logger.info(
            "[HealthCheck] AKShare: success_rate=%.0f%%, sina_fallback=%d, mock_fallback=%d, degraded=%s",
            ak_health["snapshot_success_rate"] * 100,
            ak_health["sina_fallback_count"],
            ak_health["mock_fallback_count"],
            ak_health["is_degraded"],
        )
        if ak_health["is_degraded"]:
            logger.warning("[HealthCheck] ⚠️ AKShare 数据源降级中！最近错误: %s", ak_health.get("last_error"))
    except Exception as e:
        logger.warning("[HealthCheck] AKShare 健康检查异常: %s", e)


def job_weekly_feedback() -> None:
    """每周日 20:00 复盘 — 为所有人格分别执行。"""
    logger.info("⏰ 定时任务触发: 周复盘 (多人格模式)")

    personas = list_personas()
    if not personas:
        logger.warning("未找到任何人格配置，使用默认人格复盘")
        asyncio.run(run_weekly_feedback())
    else:
        logger.info("为 %d 个人格执行周复盘", len(personas))
        for p in personas:
            persona_id = p.get("id") or p.get("persona_id")
            if not persona_id:
                continue
            persona_name = p.get("name", persona_id)
            logger.info("开始复盘人格: %s (%s)", persona_name, persona_id)
            try:
                asyncio.run(run_weekly_feedback(persona_id=persona_id))
                logger.info("人格 %s 复盘完成", persona_name)
            except Exception as e:
                logger.error("人格 %s 复盘失败: %s", persona_name, e)

    # 元规则归纳：跨人格共享的教训归纳（不区分 persona，因为交易数据是打通的）
    try:
        from src.experience_layer.meta_rule_synthesizer import get_synthesizer
        synth = get_synthesizer()
        result = synth.synthesize()
        if result:
            logger.info(
                "[MetaRule] 归纳完成: %d avoid / %d prefer | %s",
                len(result.get("avoid_patterns", [])),
                len(result.get("prefer_patterns", [])),
                result.get("summary", ""),
            )
            # 自动回写 persona.yaml（去重 + 备份 + 校验）
            try:
                from src.experience_layer.persona_updater import PersonaUpdater
                updater = PersonaUpdater()
                wb_result = updater.apply_meta_rules(result)
                if wb_result.get("updated"):
                    logger.info(
                        "[MetaRule→Persona] 回写成功: +%d avoid, +%d prefer",
                        len(wb_result.get("avoid_added", [])),
                        len(wb_result.get("prefer_added", [])),
                    )
            except Exception as e:
                logger.warning("[MetaRule→Persona] 回写失败 (不影响主流程): %s", e)
    except Exception as e:
        logger.error("[MetaRule] 归纳失败 (不影响主流程): %s", e)


@skip_if_weekend
def job_watchlist_check() -> None:
    """每日 15:40 自选股池检查 — 检查入场条件 + 剔除过期标的。"""
    logger.info("⏰ 定时任务触发: 自选股池检查")
    try:
        from src.agents.explorer.watchlist import run_watchlist_check
        result = run_watchlist_check()
        logger.info("Watchlist 检查完成: %s", result)
    except Exception as e:
        logger.warning("Watchlist 检查失败: %s", e)


@skip_if_weekend
def job_sync_sma_status() -> None:
    """每日 15:45 同步 SMA 发帖状态 — 更新 social_posts 表。"""
    logger.info("⏰ 定时任务触发: SMA 状态同步")
    try:
        from src.social_media_dispatcher.client import SmaClient
        from src.central_brain import get_central_brain

        brain = get_central_brain()
        conn = brain.store._conn()

        # 查找所有未完成的 social_posts
        pending_posts = conn.execute(
            "SELECT * FROM social_posts WHERE status NOT IN ('published', 'failed', 'error') "
            "AND dispatched_at > datetime('now', '-3 days')"
        ).fetchall()

        if not pending_posts:
            logger.info("无待同步的社交帖子")
            return

        client = SmaClient()
        updated = 0
        for post in pending_posts:
            post = dict(post)
            task_id = post.get("sma_task_id")
            if not task_id:
                continue
            try:
                resp = client.health()  # 先确认 SMA 可达
                if not resp:
                    break

                import httpx
                resp = httpx.get(
                    f"{client.base_url}/api/tasks/{task_id}",
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    new_status = data.get("status", "")
                    post_url = data.get("post_url", "")
                    if new_status and new_status != post.get("status"):
                        conn.execute(
                            "UPDATE social_posts SET status = ?, post_url = ?, updated_at = datetime('now') "
                            "WHERE sma_task_id = ?",
                            (new_status, post_url or "", task_id),
                        )
                        conn.commit()
                        updated += 1
                        logger.info(
                            "SMA 状态更新: task=%s, %s → %s",
                            task_id, post.get("status"), new_status,
                        )
            except Exception as e:
                logger.debug("SMA 状态查询失败 task=%s: %s", task_id, e)

        logger.info("SMA 状态同步完成: %d/%d 已更新", updated, len(pending_posts))
    except ImportError:
        logger.debug("SMA 客户端未配置，跳过状态同步")
    except Exception as e:
        logger.warning("SMA 状态同步异常: %s", e)


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


@skip_if_weekend
def job_stale_position_check() -> None:
    """每日 14:50 超期持仓强制复审 — 检测超期持仓并触发带卖出倾向的复审。

    与普通 intraday_review 的区别：
    - 对超期持仓注入 force_review_reason，使 LLM 更积极建议卖出
    - 在收盘前 10 分钟执行，确保卖出指令还能在当日完成
    """
    logger.info("⏰ 定时任务触发: 超期持仓强制复审")

    personas = list_personas()
    if not personas:
        stale = check_stale_positions()
        if stale:
            force_map = {p["position_id"]: p["force_review_reason"] for p in stale}
            logger.warning("发现 %d 只超期持仓，触发强制复审", len(stale))
            asyncio.run(run_intraday_review(force=True, force_review_map=force_map))
        return

    total_stale = 0
    for p in personas:
        persona_id = p["id"]
        persona_name = p.get("name", persona_id)
        try:
            stale = check_stale_positions(persona_id=persona_id)
            if not stale:
                continue
            force_map = {s["position_id"]: s["force_review_reason"] for s in stale}
            logger.warning(
                "人格 %s 有 %d 只超期持仓，触发强制复审", persona_name, len(stale),
            )
            results = asyncio.run(
                run_intraday_review(force=True, persona_id=persona_id, force_review_map=force_map)
            )
            actions = [r for r in results if r.get("action") not in ("HOLD", None)]
            if actions:
                logger.info("人格 %s 强制复审执行 %d 个卖出动作", persona_name, len(actions))
            total_stale += len(stale)
        except Exception as e:
            logger.error("人格 %s 超期强制复审失败: %s", persona_name, e)

    if total_stale > 0:
        logger.warning("总计 %d 只超期持仓已触发强制复审", total_stale)


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

    # 盘中每 5 分钟 regime drift 快速检测 (9:35 - 14:55)
    for hour in range(9, 15):
        for minute in range(0, 60, 5):
            if hour == 9 and minute < 35:
                continue
            if hour >= 12 and hour < 13:
                continue
            if hour == 14 and minute > 55:
                continue
            t = f"{hour:02d}:{minute:02d}"
            schedule.every().day.at(t).do(job_regime_drift_check)

    schedule.every().day.at("09:35").do(job_market_brain_only)
    schedule.every().day.at("11:35").do(job_market_brain_only)
    schedule.every().day.at("14:00").do(job_market_brain_only)

    schedule.every().day.at("15:05").do(job_closing_analysis)
    schedule.every().day.at("15:15").do(job_backfill_attributions)
    schedule.every().day.at("15:35").do(job_daily_pipeline)
    schedule.every().day.at("15:40").do(job_watchlist_check)
    schedule.every().day.at("15:45").do(job_sync_sma_status)
    schedule.every().day.at("14:50").do(job_stale_position_check)
    schedule.every().day.at("16:00").do(job_health_check)

    schedule.every().sunday.at("20:00").do(job_weekly_feedback)

    logger.info("调度器已启动 — 当前时间: %s", datetime.now().isoformat())

    for job in schedule.get_jobs():
        logger.info("任务注册: %s | 下次运行: %s", job, job.next_run)


def run_scheduler() -> None:
    """主循环。"""
    setup_schedule()

    shutdown_requested = False

    def _signal_handler(signum, frame):
        nonlocal shutdown_requested
        logger.info("收到信号 %d，准备优雅停机...", signum)
        shutdown_requested = True

    signal_mod.signal(signal_mod.SIGTERM, _signal_handler)
    signal_mod.signal(signal_mod.SIGINT, _signal_handler)

    heartbeat_path = Path("/app/data/scheduler.heartbeat" if os.getenv("DB_PATH") else "data/scheduler.heartbeat")
    last_heartbeat = 0

    while not shutdown_requested:
        schedule.run_pending()

        now = time.time()
        if now - last_heartbeat > 300:
            logger.info("scheduler heartbeat — alive")
            last_heartbeat = now
            try:
                heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
                heartbeat_path.touch()
            except OSError:
                pass

        time.sleep(30)

    logger.info("调度器已停止")


if __name__ == "__main__":
    setup_logging()
    run_scheduler()
