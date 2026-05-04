from __future__ import annotations

from typing import Dict, Any

from stock_analyzer.pipelines import DailyScanPipeline

from .workflow_engine import Workflow


workflow = Workflow("daily_market_workflow")


@workflow.step("scan_market")
def scan_market(ctx: Dict[str, Any]) -> Dict[str, Any]:
    pipeline = DailyScanPipeline(
        trading_agent_url=ctx.get("trading_agent_url"),
        max_agent_calls=ctx.get("max_agent_calls", 8),
        sma_base_url=None,
        sma_account_id=None,
    )

    picks = pipeline.run()

    ctx["picks"] = picks

    return ctx


@workflow.step("publish_event")
def publish_event(ctx: Dict[str, Any]) -> Dict[str, Any]:
    event_bus = ctx.get("event_bus")

    if event_bus:
        event_bus.publish(
            "daily.picks.generated",
            {
                "date": str(ctx["picks"].pick_date),
                "num_candidates": len(ctx["picks"].candidates),
                "picks": ctx["picks"],
            },
        )

    return ctx
