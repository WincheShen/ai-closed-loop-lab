#!/usr/bin/env python3
"""SMA 同步器 (Phase 3.5 S3) —— 从 Social-media-automation 拉取互动数据更新到 ai-lab。

功能：
1. 找出 ai-lab 中 social_posts.sma_status != 'completed' 的记录
2. attach SMA 的 web_tasks.db + monitor_tasks.db (只读)
3. 拉取 status / post_url / metrics_json
4. 更新 ai-lab social_posts 表

用法：
    # 手动跑一次
    python scripts/sync_sma_engagements.py

    # 每 30 分钟自动同步 (cron)
    */30 * * * * cd /Users/neo/Projects/ai-closed-loop-lab && /opt/homebrew/Caskroom/miniforge/base/envs/ai-lab/bin/python scripts/sync_sma_engagements.py >> logs/sma_sync.log 2>&1
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 项目路径设置
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from central_brain.metadata_store import get_central_brain  # noqa: E402

logger = logging.getLogger(__name__)

# SMA 数据库路径 (相对于 ai-lab 项目)
# 假设 SMA 项目在 ~/Projects/Social-media-automation/
SMA_PROJECT_PATH = Path.home() / "Projects" / "Social-media-automation"
SMA_WEB_TASKS_DB = SMA_PROJECT_PATH / "data" / "state" / "web_tasks.db"
SMA_MONITOR_DB = SMA_PROJECT_PATH / "data" / "state" / "monitor_tasks.db"


def _sma_db_path(path: Path, env_var: str) -> Path:
    """优先从环境变量读取 SMA 数据库路径。"""
    from_env = __import__("os").environ.get(env_var)
    if from_env:
        return Path(from_env)
    return path


def sync_engagements(dry_run: bool = False) -> dict:
    """执行同步，返回统计信息。"""
    stats = {"checked": 0, "updated": 0, "errors": 0, "skipped": 0}

    # 获取 ai-lab central_brain
    brain = get_central_brain()
    store = brain.store

    # 查找待同步的记录 (status 不是 completed 或没有 post_url 的)
    pending = store.list_social_posts(limit=1000)
    to_check = [p for p in pending if p.get("sma_status") != "completed" or not p.get("post_url")]
    stats["checked"] = len(to_check)

    if not to_check:
        logger.info("No pending social posts to sync.")
        return stats

    # 确认 SMA 数据库存在
    web_db_path = _sma_db_path(SMA_WEB_TASKS_DB, "SMA_WEB_TASKS_DB")
    monitor_db_path = _sma_db_path(SMA_MONITOR_DB, "SMA_MONITOR_DB")

    if not web_db_path.exists():
        logger.error("SMA web_tasks.db not found: %s", web_db_path)
        stats["errors"] += 1
        return stats

    # 连接 ai-lab central_brain.db (用于更新)
    ai_lab_db = store.db_path

    # 使用 attach 方式同时连接三个数据库
    conn = sqlite3.connect(ai_lab_db)
    conn.row_factory = sqlite3.Row

    try:
        # Attach SMA 数据库
        conn.execute(f"ATTACH DATABASE ? AS sma_web", (str(web_db_path),))
        logger.info("Attached SMA web_tasks.db: %s", web_db_path)

        if monitor_db_path.exists():
            conn.execute(f"ATTACH DATABASE ? AS sma_monitor", (str(monitor_db_path),))
            logger.info("Attached SMA monitor_tasks.db: %s", monitor_db_path)
        else:
            logger.warning("SMA monitor_tasks.db not found: %s", monitor_db_path)

        for post in to_check:
            task_id = post["sma_task_id"]
            try:
                # 从 SMA web_tasks.tasks 表查状态
                row = conn.execute(
                    "SELECT status, post_url, error FROM sma_web.tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()

                if not row:
                    logger.warning("Task %s not found in SMA db", task_id)
                    stats["skipped"] += 1
                    continue

                sma_status = row["status"]
                post_url = row["post_url"]
                error_msg = row["error"]

                # 从 SMA monitor_tasks 查互动数据
                metrics_json = None
                if monitor_db_path.exists() and post_url:
                    mrow = conn.execute(
                        "SELECT metrics_json, status FROM sma_monitor.monitor_tasks WHERE post_url = ? ORDER BY updated_at DESC LIMIT 1",
                        (post_url,),
                    ).fetchone()
                    if mrow:
                        metrics_json = mrow["metrics_json"]

                # 更新 ai-lab
                if not dry_run:
                    store.update_social_post_metrics(
                        sma_task_id=task_id,
                        sma_status=sma_status,
                        post_url=post_url,
                        last_metrics=metrics_json,
                        error=error_msg,
                    )
                    logger.info(
                        "Updated %s: status=%s, has_metrics=%s",
                        task_id, sma_status, bool(metrics_json)
                    )
                else:
                    logger.info(
                        "[DRY] Would update %s: status=%s, post_url=%s, metrics=%s",
                        task_id, sma_status, post_url, bool(metrics_json)
                    )
                stats["updated"] += 1

            except Exception as e:
                logger.error("Failed to sync task %s: %s", task_id, e)
                stats["errors"] += 1

    finally:
        conn.close()

    return stats


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="只打印不实际更新")
    p.add_argument("--sma-web-db", type=Path, help="SMA web_tasks.db 路径")
    p.add_argument("--sma-monitor-db", type=Path, help="SMA monitor_tasks.db 路径")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # 允许命令行覆盖默认路径
    if args.sma_web_db:
        global SMA_WEB_TASKS_DB
        SMA_WEB_TASKS_DB = args.sma_web_db
    if args.sma_monitor_db:
        global SMA_MONITOR_DB
        SMA_MONITOR_DB = args.sma_monitor_db

    logger.info("Starting SMA engagement sync...")
    stats = sync_engagements(dry_run=args.dry_run)
    logger.info("Sync complete: %s", stats)

    # 打印摘要到 stdout
    print(f"\n同步完成: 检查 {stats['checked']} 条, 更新 {stats['updated']} 条, 跳过 {stats['skipped']} 条, 错误 {stats['errors']} 条")
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
