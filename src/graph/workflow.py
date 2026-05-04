"""Main Workflow — LangGraph 编排四大 Agent 簇。

日常交易流 (Daily):
    Explorer → Strategist → Executioner → Influencer

周末复盘流 (Weekly):
    FeedbackLoop (Backtest + Prompt Evolution)

两种入口：
    - run_daily_pipeline()  — 日常扫描与执行
    - run_weekly_feedback() — 周末复盘与进化
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from langgraph.graph import END, StateGraph

from src.agents.explorer.scanner import run_discovery_node
from src.agents.executioner.executor import run_execution_node
from src.agents.influencer.content_engine import run_influencer_node
from src.agents.strategist.signal_generator import run_strategy_node
from src.central_brain import get_central_brain
from src.feedback_loop.backtest_engine import run_weekly_feedback_node
from src.graph.state import TradingState, create_empty_state
from src.infra.config import cfg
from src.infra.logger import get_agent_logger, setup_logging

logger = get_agent_logger("workflow", "init")


# =============================================================================
# 条件路由函数
# =============================================================================

def _route_after_explorer(state: TradingState) -> str:
    """探索后路由：有候选票则去决策，否则结束。"""
    if state.get("target_stocks"):
        return "strategist"
    logger.warning("Explorer 未产出候选票，流程终止")
    return END


def _route_after_strategist(state: TradingState) -> str:
    """决策后路由：有信号则去执行，否则结束。"""
    if state.get("trade_signals"):
        return "executioner"
    logger.warning("Strategist 未生成信号，流程终止")
    return END


def _route_after_executioner(state: TradingState) -> str:
    """执行后路由：有成交则去社交媒体，否则结束。"""
    if state.get("filled_orders"):
        return "influencer"
    logger.info("Executioner 无成交，跳过社交媒体")
    return END


def _route_after_influencer(state: TradingState) -> str:
    """社交媒体后路由：直接结束（反馈循环是独立周任务）。"""
    return END


# =============================================================================
# Graph 构建
# =============================================================================

def build_daily_graph() -> StateGraph:
    """构建日常交易流 LangGraph。"""
    graph = StateGraph(TradingState)

    # 注册节点
    graph.add_node("explorer", run_discovery_node)
    graph.add_node("strategist", run_strategy_node)
    graph.add_node("executioner", run_execution_node)
    graph.add_node("influencer", run_influencer_node)

    # 入口
    graph.set_entry_point("explorer")

    # 边
    graph.add_conditional_edges("explorer", _route_after_explorer)
    graph.add_conditional_edges("strategist", _route_after_strategist)
    graph.add_conditional_edges("executioner", _route_after_executioner)
    graph.add_edge("influencer", END)

    return graph.compile()


def build_weekly_graph() -> StateGraph:
    """构建周末复盘流 LangGraph。"""
    graph = StateGraph(TradingState)
    graph.add_node("feedback_loop", run_weekly_feedback_node)
    graph.set_entry_point("feedback_loop")
    graph.add_edge("feedback_loop", END)
    return graph.compile()


# =============================================================================
# 运行入口
# =============================================================================

async def run_daily_pipeline(run_mode: str = "mock") -> TradingState:
    """运行一次完整的日常交易闭环。

    Args:
        run_mode: "scan"(只扫描) / "mock"(模拟盘) / "paper"(模拟盘) / "live"(实盘)
    """
    setup_logging()
    session_id = f"daily-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    logger.info("=" * 60)
    logger.info("🚀 启动日常交易流 — session=%s, mode=%s", session_id, run_mode)
    logger.info("=" * 60)

    initial_state = create_empty_state(session_id, run_mode)
    graph = build_daily_graph()

    result = await graph.ainvoke(initial_state)

    # 持久化最终状态
    get_central_brain().persist_state(session_id, run_mode, result)

    logger.info("=" * 60)
    logger.info("✅ 日常交易流完成 — session=%s", session_id)
    logger.info("   候选票: %d", len(result.get("target_stocks", [])))
    logger.info("   交易信号: %d", len(result.get("trade_signals", [])))
    logger.info("   成交: %d", len(result.get("filled_orders", [])))
    logger.info("   发布: %d", len(result.get("published_posts", [])))
    logger.info("=" * 60)

    return result


async def run_weekly_feedback() -> TradingState:
    """运行一次周末复盘与 Prompt 进化。"""
    setup_logging()
    session_id = f"weekly-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    logger.info("=" * 60)
    logger.info("🔁 启动周末复盘 — session=%s", session_id)
    logger.info("=" * 60)

    initial_state = create_empty_state(session_id, "feedback")
    graph = build_weekly_graph()

    result = await graph.ainvoke(initial_state)

    # 持久化
    get_central_brain().persist_state(session_id, "feedback", result)

    # Prompt 进化
    from src.feedback_loop.prompt_evolution import PromptEvolution

    evo = PromptEvolution(session_id)
    records = result.get("performance_log", [])
    if records:
        weights = evo.update_weights_from_records(records)
        prompt_snippet = evo.generate_evolution_prompt(weights)
        logger.info("🧬 Prompt 进化建议：\n%s", prompt_snippet)

    logger.info("=" * 60)
    logger.info("✅ 周末复盘完成 — session=%s", session_id)
    logger.info("=" * 60)

    return result


# =============================================================================
# CLI 入口
# =============================================================================

async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="AI Closed Loop Lab")
    parser.add_argument(
        "--mode",
        choices=["scan", "mock", "paper", "live", "feedback"],
        default="mock",
        help="运行模式",
    )
    args = parser.parse_args()

    if args.mode == "feedback":
        await run_weekly_feedback()
    else:
        await run_daily_pipeline(args.mode)


if __name__ == "__main__":
    asyncio.run(main())
