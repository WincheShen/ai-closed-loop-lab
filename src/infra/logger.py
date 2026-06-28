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
_logging_initialized = False


def setup_logging(
    level: str | None = None,
    log_dir: Path | None = None,
    session_id: str | None = None,
) -> None:
    """初始化全局日志系统。

    同时输出到：
    - 终端 (RichHandler, 带颜色)
    - 文件 (data/logs/YYYY-MM-DD_{session_id}.log)

    首次调用：清除默认 handler，添加 Rich + 文件 handler。
    后续调用（如 pipeline 每次运行）：仅追加新的 session 文件 handler，
    不清除已有的 Rich handler 和调度器文件 handler，避免破坏上层日志。
    """
    global _logging_initialized
    effective_level = (level or cfg().get("log_level", "INFO")).upper()

    root = logging.getLogger()
    root.setLevel(effective_level)

    if not _logging_initialized:
        # 首次：清除默认 handler，添加 Rich 终端输出
        for h in root.handlers[:]:
            root.removeHandler(h)

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
        _logging_initialized = True
    else:
        # 后续调用：更新 handler 日志级别，不清除
        for h in root.handlers:
            if isinstance(h, RichHandler):
                h.setLevel(effective_level)

    # 文件输出 — 每个 session 一个独立日志文件
    if log_dir is None:
        log_dir = Path(cfg().get("data_dir", "data")) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    suffix = f"_{session_id}" if session_id else ""
    file_path = log_dir / f"{today}{suffix}.log"

    # 避免同一文件路径重复添加 handler
    existing_files = {
        h.baseFilename
        for h in root.handlers
        if isinstance(h, logging.FileHandler) and hasattr(h, "baseFilename")
    }
    resolved = str(file_path.resolve())
    if resolved not in existing_files:
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
