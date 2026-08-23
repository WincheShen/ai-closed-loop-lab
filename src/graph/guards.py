"""LangGraph 节点错误边界。异常记事件、降级 state，不炸掉整日管线。"""
from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from src.graph.state import TradingState

logger = logging.getLogger(__name__)

# market_brain / explorer 失败 → 清空下游开仓输入
# influencer 失败 → 忽略
# 其余 → 清空本层产出
_CLEAR_ON_FAIL: dict[str, dict[str, Any]] = {
    "market_brain": {
        "market_regime": {"regime": "unknown", "recommended_posture": "exit",
                          "data_quality": "unavailable"},
        "target_stocks": [],
        "trade_signals": [],
        "hot_sectors": [],
    },
    "explorer": {"target_stocks": [], "trade_signals": []},
    "strategist": {"trade_signals": []},
    "risk_governor": {"trade_signals": []},
    "executioner": {"filled_orders": []},
    "influencer": {},
}


def node_guard(name: str) -> Callable:
    def deco(fn: Callable[[TradingState], dict[str, Any]]):
        @wraps(fn)
        def wrapped(state: TradingState) -> dict[str, Any]:
            try:
                return fn(state)
            except Exception as exc:
                logger.exception("node %s failed", name)
                try:
                    from src.central_brain import get_central_brain
                    get_central_brain().log_agent_event(
                        state.get("session_id", ""),
                        name,
                        "node_failed",
                        {"error": str(exc)},
                    )
                except Exception:
                    logger.warning("node_failed 事件写入失败", exc_info=True)
                degraded = dict(_CLEAR_ON_FAIL.get(name, {}))
                errors = list(state.get("errors") or [])
                errors.append({"node": name, "error": str(exc)})
                logs = list(state.get("logs") or [])
                logs.append(f"[{name}] FAILED: {exc}")
                degraded["errors"] = errors
                degraded["logs"] = logs
                return degraded
        return wrapped
    return deco
