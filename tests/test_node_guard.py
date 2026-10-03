"""node_guard 异常降级。"""
from __future__ import annotations

import asyncio
import inspect

from src.graph.guards import node_guard
from src.graph.state import create_empty_state
from src.graph.workflow import _route_after_market_brain
from langgraph.graph import END


def test_node_guard_swallows_and_records() -> None:
    @node_guard("explorer")
    def boom(state):
        raise RuntimeError("scan down")

    state = create_empty_state("s1", "mock")
    out = boom(state)
    assert out["target_stocks"] == []
    assert out["errors"][0]["node"] == "explorer"
    assert "scan down" in out["errors"][0]["error"]


def test_node_guard_async_node_returns_awaitable_dict() -> None:
    # 回归：sync wrapper 会把 async 节点的协程对象直接返回，
    # LangGraph 写 state 时抛 InvalidUpdateError，整日管线崩在 executioner。
    @node_guard("executioner")
    async def ok(state):
        return {"filled_orders": ["ord-1"]}

    assert inspect.iscoroutinefunction(ok)
    state = create_empty_state("s1", "mock")
    out = asyncio.run(ok(state))
    assert out["filled_orders"] == ["ord-1"]


def test_node_guard_async_failure_degrades() -> None:
    @node_guard("executioner")
    async def boom(state):
        raise RuntimeError("exec down")

    state = create_empty_state("s2", "mock")
    out = asyncio.run(boom(state))
    assert out["filled_orders"] == []
    assert out["errors"][0]["node"] == "executioner"
    assert "exec down" in out["errors"][0]["error"]


def test_route_stops_when_market_brain_failed() -> None:
    state = create_empty_state("s1", "mock")
    state["errors"] = [{"node": "market_brain", "error": "x"}]
    assert _route_after_market_brain(state) == END


def test_route_stops_on_unavailable_quality() -> None:
    state = create_empty_state("s1", "mock")
    state["market_regime"] = {
        "regime": "neutral",
        "recommended_posture": "selective_attack",
        "data_quality": "unavailable",
    }
    assert _route_after_market_brain(state) == END
