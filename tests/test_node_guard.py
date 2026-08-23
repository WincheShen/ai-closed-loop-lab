"""node_guard 异常降级。"""
from __future__ import annotations

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
