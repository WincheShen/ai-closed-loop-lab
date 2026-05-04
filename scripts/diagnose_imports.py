"""诊断脚本：单步 import，定位卡在哪一步。

每一步打印自带 flush，并各自有 8s 超时。
跑这个之前**先 ping 一下，看哪一步开始卡**。

用法：
    python scripts/diagnose_imports.py
"""
from __future__ import annotations

import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

STEP_TIMEOUT = 8


def step(name: str, fn):
    print(f"[{time.strftime('%H:%M:%S')}] → {name} ...", end="", flush=True)
    sys.stdout.flush()

    def handler(s, f):
        raise TimeoutError(f"{name} 超过 {STEP_TIMEOUT}s")

    signal.signal(signal.SIGALRM, handler)
    signal.alarm(STEP_TIMEOUT)
    t0 = time.time()
    try:
        fn()
    except Exception as e:
        signal.alarm(0)
        dt = time.time() - t0
        print(f" ✗ {dt:.2f}s  {type(e).__name__}: {e}")
        sys.exit(1)
    signal.alarm(0)
    print(f" ✓ {time.time() - t0:.2f}s")


# 自顶向下逐步 import，逐一打印
print(f"Python: {sys.version}")
print(f"sys.path[0]: {sys.path[0]}")
print()

step("import pydantic", lambda: __import__("pydantic"))
step("import httpx", lambda: __import__("httpx"))

step("stock_analyzer.data_source.akshare_client",
     lambda: __import__("stock_analyzer.data_source.akshare_client",
                        fromlist=["StockQuote"]))
step("stock_analyzer.data_source.hot_sector_detector",
     lambda: __import__("stock_analyzer.data_source.hot_sector_detector",
                        fromlist=["HotSectorDetector"]))
step("stock_analyzer.data_source (package)",
     lambda: __import__("stock_analyzer.data_source"))
step("stock_analyzer.rules.rule_engine",
     lambda: __import__("stock_analyzer.rules.rule_engine",
                        fromlist=["RuleEngine"]))
step("stock_analyzer.rules.builtin",
     lambda: __import__("stock_analyzer.rules.builtin"))
step("stock_analyzer.rules (package)",
     lambda: __import__("stock_analyzer.rules"))
step("stock_analyzer.pipelines.daily_scan",
     lambda: __import__("stock_analyzer.pipelines.daily_scan",
                        fromlist=["DailyPicks"]))

step("social_media_dispatcher.schemas",
     lambda: __import__("social_media_dispatcher.schemas",
                        fromlist=["TopicPayload"]))
step("social_media_dispatcher.topic_router",
     lambda: __import__("social_media_dispatcher.topic_router",
                        fromlist=["TopicRouter"]))
step("social_media_dispatcher.client",
     lambda: __import__("social_media_dispatcher.client",
                        fromlist=["SmaClient"]))
step("social_media_dispatcher (package)",
     lambda: __import__("social_media_dispatcher"))

print()
print("✅ 所有 import 步骤通过")
