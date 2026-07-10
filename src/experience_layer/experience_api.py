"""ExperienceAPI — 经验层统一入口。

提供一站式接口供各 Agent 查询交易经验，
同时提供 record() 方法在平仓时统一更新所有经验维度。
"""

from __future__ import annotations

from typing import Any

from src.experience_layer.strategy_ledger import StrategyLedger
from src.experience_layer.stock_memory import StockMemory
from src.experience_layer.dynamic_blacklist import DynamicBlacklist
from src.infra.logger import get_agent_logger

logger = get_agent_logger("experience", "api")


class ExperienceAPI:
    """经验层统一门面。"""

    def __init__(self) -> None:
        self.strategy_ledger = StrategyLedger()
        self.stock_memory = StockMemory()

    def blacklist_for(self, persona_id: str | None = None) -> DynamicBlacklist:
        """获取针对特定人格的黑名单实例。"""
        return DynamicBlacklist(persona_id=persona_id)

    # ── 查询接口 ──────────────────────────────────────────────────────

    def get_stock_history(self, symbol: str) -> dict[str, Any] | None:
        """查询个股交易记忆。"""
        return self.stock_memory.get_history(symbol)

    def get_strategy_stats(
        self, strategy_id: str, regime: str | None = None,
    ) -> dict[str, Any] | None:
        """查询策略胜率统计。"""
        return self.strategy_ledger.get_stats(strategy_id, regime=regime)

    def get_all_strategy_stats(
        self, regime: str | None = None,
    ) -> list[dict[str, Any]]:
        """查询所有策略统计。"""
        return self.strategy_ledger.get_all_stats(regime=regime)

    def get_regime_matrix(self) -> dict:
        """获取策略×regime 胜率矩阵。"""
        return self.strategy_ledger.get_regime_matrix()

    def is_stock_in_cooldown(self, symbol: str) -> bool:
        """判断个股是否在冷却期。"""
        return self.stock_memory.is_in_cooldown(symbol)

    # ── Prompt 生成接口 ───────────────────────────────────────────────

    def strategy_prompt_block(self, regime: str | None = None) -> str:
        """生成策略表现 prompt 片段（给 Strategist 用）。"""
        return self.strategy_ledger.generate_prompt_block(regime=regime)

    def stock_prompt_block(self, symbol: str) -> str:
        """生成个股记忆 prompt 片段（给 Strategist 用）。"""
        return self.stock_memory.generate_prompt_block(symbol)

    # ── 规则引擎接口 ─────────────────────────────────────────────────

    def get_rule_weight_multiplier(
        self, strategy_id: str, regime: str | None = None,
    ) -> float:
        """获取规则权重乘数（给 Explorer 规则引擎用）。

        Returns:
            0.3~1.8 的乘数，1.0 表示无调整。
        """
        stats = self.strategy_ledger.get_stats(strategy_id, regime=regime)
        if not stats:
            return 1.0
        return stats["weight_multiplier"]
