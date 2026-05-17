"""Cognitive Agent — CIO 层（Chief Investment Officer）。

包含：
- TradingPersona: 投资人格配置加载
- MarketBrain: 市场世界模型，输出 regime + daily plan
"""

from src.agents.cio.trading_persona import (
    TradingPersona,
    get_persona,
    load_persona,
)

__all__ = ["TradingPersona", "load_persona", "get_persona"]
