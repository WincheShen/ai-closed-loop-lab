"""启动 Webhook Listener。

用法：
    python scripts/run_webhook_listener.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import uvicorn  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    port = int(os.environ.get("WEBHOOK_PORT", "8002"))
    host = os.environ.get("WEBHOOK_HOST", "127.0.0.1")
    uvicorn.run(
        "webhook_listener.server:app",
        host=host, port=port, reload=False,
    )


if __name__ == "__main__":
    main()
