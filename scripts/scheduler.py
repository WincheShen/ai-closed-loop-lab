#!/usr/bin/env python3
"""定时调度脚本 — 基于 schedule 库的日常任务调度。

部署建议：
    - 开发调试：直接运行 python scripts/scheduler.py
    - 生产部署：systemd 服务或 Docker 容器后台运行
    - 也可改用 APScheduler/Celery 替代
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging
import time

import schedule

from src.graph.workflow import run_daily_pipeline, run_weekly_feedback
from src.infra.logger import setup_logging

logger = logging.getLogger(__name__)


# --- 定时任务 ---

def job_daily_scan() -> None:
    """每日 15:35 收盘后扫描。"""
    logger.info("⏰ 定时任务触发: 每日扫描")
    asyncio.run(run_daily_pipeline("scan"))


def job_daily_mock() -> None:
    """每日 15:40 模拟盘闭环。"""
    logger.info("⏰ 定时任务触发: 模拟盘闭环")
    asyncio.run(run_daily_pipeline("mock"))


def job_weekly_feedback() -> None:
    """每周日 20:00 复盘。"""
    logger.info("⏰ 定时任务触发: 周复盘")
    asyncio.run(run_weekly_feedback())


# --- 调度配置 ---

def setup_schedule() -> None:
    """配置所有定时任务。"""
    # 每日收盘后扫描 (15:35)
    schedule.every().day.at("15:35").do(job_daily_scan)

    # 每日收盘后模拟盘闭环 (15:40)
    schedule.every().day.at("15:40").do(job_daily_mock)

    # 每周日 20:00 复盘
    schedule.every().sunday.at("20:00").do(job_weekly_feedback)

    logger.info("调度器已启动 — 任务列表:")
    for job in schedule.get_jobs():
        logger.info("  • %s", job)


def run_scheduler() -> None:
    """主循环。"""
    setup_schedule()
    while True:
        schedule.run_pending()
        time.sleep(30)  # 每 30 秒检查一次


if __name__ == "__main__":
    setup_logging()
    run_scheduler()
