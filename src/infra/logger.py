"""结构化日志 — 为每个 Agent 簇分配独立 Logger。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

from src.infra.config import cfg

_CONSOLE = Console(stderr=True)


def setup_logging(
    level: str | None = None,
    log_dir: Path | None = None,
    session_id: str | None = None,
) -> None:
    """初始化全局日志系统。

    同时输出到：
    - 终端 (RichHandler, 带颜色)
    - 文件 (data/logs/YYYY-MM-DD_{session_id}.log)
    """
    effective_level = (level or cfg().get("log_level", "INFO")).upper()

    root = logging.getLogger()
    root.setLevel(effective_level)

    # 清除已有 handler，避免重复
    for h in root.handlers[:]:
        root.removeHandler(h)

    # 1. Rich 终端输出
    rich_handler = RichHandler(
        console=_CONSOLE,
        rich_tracebacks=True,
        markup=True,
        show_path=False,
    )
    rich_handler.setLevel(effective_level)
    fmt = logging.Formatter(
        fmt="%(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    rich_handler.setFormatter(fmt)
    root.addHandler(rich_handler)

    # 2. 文件输出 (JSON Lines 或纯文本)
    if log_dir is None:
        log_dir = Path(cfg().get("data_dir", "data")) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    suffix = f"_{session_id}" if session_id else ""
    file_path = log_dir / f"{today}{suffix}.log"

    file_handler = logging.FileHandler(file_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )
    file_handler.setFormatter(file_fmt)
    root.addHandler(file_handler)

    logging.info("Logging initialized — level=%s, file=%s", effective_level, file_path)


def get_logger(name: str) -> logging.Logger:
    """按 Agent 簇名称获取 Logger。

    命名约定：
        src.agents.explorer    → 探索者
        src.agents.strategist  → 决策者
        src.agents.executioner → 执行者
        src.agents.influencer  → 社交媒体
        src.central_brain      → 元数据中心
        src.feedback_loop      → 反馈循环
    """
    return logging.getLogger(name)


class AgentLoggerAdapter(logging.LoggerAdapter):
    """为每条日志自动注入 session_id 和 agent_name。"""

    def __init__(self, logger: logging.Logger, session_id: str, agent_name: str):
        super().__init__(logger, {})
        self.session_id = session_id
        self.agent_name = agent_name

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        msg = f"[{self.agent_name}:{self.session_id[:8]}] {msg}"
        return msg, kwargs


def get_agent_logger(agent_name: str, session_id: str) -> AgentLoggerAdapter:
    """获取带 Agent 标识的 Logger。"""
    logger = get_logger(f"src.agents.{agent_name}")
    return AgentLoggerAdapter(logger, session_id, agent_name)
