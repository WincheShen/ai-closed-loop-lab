"""启动 TradingAgent HTTP 服务。

用法：
    python scripts/run_trading_agent_service.py          # 默认 :8001 + auto analyzer
    TAS_ANALYZER=mock python scripts/run_trading_agent_service.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# 让 `python scripts/xxx.py` 也能 import src 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# 加载 .env（OPENAI_API_KEY / OPENAI_BASE_URL / TAS_TA_* 等）
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import uvicorn  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("TAS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TAS_PORT", "8001")))
    args = parser.parse_args()

    uvicorn.run(
        "trading_agent_service.api.server:app",
        host=args.host, port=args.port, reload=False,
    )


if __name__ == "__main__":
    main()
