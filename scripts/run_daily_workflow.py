"""旧 ai_platform WorkflowEngine 入口已废弃，转发到 LangGraph 日管线。"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.graph.workflow import run_daily_pipeline

logging.basicConfig(level=logging.INFO)


def main():
    logging.getLogger(__name__).warning(
        "run_daily_workflow 已废弃，转发 run_daily_pipeline(mock)"
    )
    asyncio.run(run_daily_pipeline("mock"))


if __name__ == "__main__":
    main()
