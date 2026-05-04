"""分析能力适配器。

策略：
- 优先调用 third_party/tradingAgents_neo
- 若不可用（未集成 / LLM 不可达），降级到 MockAnalyzer 让流水线先跑通
- 适配器输出统一为 Report Pydantic 对象
"""
from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod
from datetime import date, timedelta
from typing import Literal

from ..api.schemas import (
    FundamentalAnalysis,
    Report,
    TechnicalAnalysis,
)

logger = logging.getLogger(__name__)


class AnalyzerAdapter(ABC):
    name: str

    @abstractmethod
    def analyze(
        self,
        symbol: str,
        depth: Literal["deep", "quick"] = "deep",
    ) -> Report: ...


# ---------------------------------------------------------------------------
# Mock 实现：保证 Phase1 闭环可跑通
# ---------------------------------------------------------------------------

class MockAnalyzer(AnalyzerAdapter):
    """假数据生成器，用于在未接入真 LLM 前打通整条流水线。"""

    name = "mock"

    def analyze(self, symbol: str, depth: Literal["deep", "quick"] = "deep") -> Report:
        rng = random.Random(hash((symbol, date.today().isoformat())))
        price = round(rng.uniform(8, 120), 2)
        decision = rng.choice(["BUY", "HOLD", "SELL"])
        delta = round(price * 0.05, 2)  # 5% 区间触发再评估
        valid_days = 3 if depth == "deep" else 1

        return Report(
            symbol=symbol,
            name=f"模拟股{symbol[-3:]}",
            current_price=price,
            summary=f"[MOCK] {symbol} 当前 {price:.2f}，建议 {decision}",
            technical=TechnicalAnalysis(
                trend=rng.choice(["上行", "震荡", "下行"]),
                key_levels={
                    "support": round(price * 0.95, 2),
                    "resistance": round(price * 1.05, 2),
                },
                summary="MA5/MA10/MA20 多头排列（模拟）",
            ),
            fundamental=FundamentalAnalysis(
                industry="模拟行业",
                market_cap_yi=round(rng.uniform(50, 5000), 1),
                pe_ttm=round(rng.uniform(8, 60), 2),
                pb=round(rng.uniform(0.8, 12), 2),
                roe=round(rng.uniform(2, 25), 2),
                summary="基本面稳健（模拟）",
            ),
            bull_case="行业景气度向上 + 技术面突破（模拟多方）",
            bear_case="估值偏高 + 大盘系统性风险（模拟空方）",
            final_decision=decision,  # type: ignore[arg-type]
            confidence=round(rng.uniform(0.55, 0.85), 2),
            reevaluation_price_range=(round(price - delta, 2), round(price + delta, 2)),
            valid_until=date.today() + timedelta(days=valid_days),
            risk_warning="本报告由 MockAnalyzer 生成，仅供联调使用，不构成投资建议。",
        )


# ---------------------------------------------------------------------------
# tradingagents_neo 适配器（Phase 3 真实接入）
# ---------------------------------------------------------------------------
# 实现在 .tradingagents_adapter 子模块，避免在没装 tradingagents 时
# import adapter.py 就直接报错。

def _load_real_adapter() -> AnalyzerAdapter | None:
    """Lazy load the real adapter, returning None if dependencies missing."""
    try:
        from .tradingagents_adapter import RealTradingAgentsAdapter
        adapter = RealTradingAgentsAdapter()
        return adapter if adapter.available else None
    except ImportError as e:
        logger.warning("RealTradingAgentsAdapter unavailable: %s", e)
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_analyzer(prefer: str = "auto") -> AnalyzerAdapter:
    """获取分析器实例。

    prefer:
        - "auto"           优先 tradingagents，不可用则 mock
        - "mock"           强制 mock
        - "tradingagents"  强制真实分析器（不可用则抛错）
    """
    if prefer == "mock":
        return MockAnalyzer()

    if prefer == "tradingagents":
        adapter = _load_real_adapter()
        if adapter is None:
            raise RuntimeError(
                "tradingagents 不可用。请先：\n"
                "  pip install -e /Users/neo/Projects/tradingAgents_neo\n"
                "并设置 OPENAI_API_KEY 等环境变量。"
            )
        return adapter

    # auto
    adapter = _load_real_adapter()
    if adapter is not None:
        logger.info("使用 RealTradingAgentsAdapter")
        return adapter
    logger.info("使用 MockAnalyzer（tradingagents 不可用）")
    return MockAnalyzer()
