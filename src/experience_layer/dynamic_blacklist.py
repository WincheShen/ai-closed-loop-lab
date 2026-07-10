"""DynamicBlacklist — 动态黑名单。

自动规则：
1. 个股连续2次止损 → 冷却5天
2. 策略+regime 胜率<30% 且样本>=3 → 暂停该策略在该 regime
3. 当前持仓中已有的股票 → 不重复选入

所有判断基于 trade_attributions 表和 positions 表实时查询。
"""

from __future__ import annotations

from typing import Any

from src.central_brain import get_central_brain
from src.experience_layer.stock_memory import StockMemory
from src.experience_layer.strategy_ledger import StrategyLedger
from src.infra.logger import get_agent_logger

logger = get_agent_logger("experience", "blacklist")


class DynamicBlacklist:
    """动态黑名单 — 基于交易经验自动屏蔽。"""

    def __init__(self, persona_id: str | None = None) -> None:
        self.brain = get_central_brain()
        self.persona_id = persona_id
        self.stock_memory = StockMemory()
        self.strategy_ledger = StrategyLedger()

    def is_blocked(
        self,
        symbol: str,
        regime: str | None = None,
    ) -> tuple[bool, str]:
        """检查某只股票是否应被屏蔽。

        Returns:
            (is_blocked, reason)
        """
        # 1. 个股冷却期
        reason = self.stock_memory.get_cooldown_reason(symbol)
        if reason:
            return True, reason

        # 2. 当前已持仓 → 不重复买入
        if self.persona_id:
            open_positions = self.brain.store.list_open_positions(
                persona_id=self.persona_id
            )
            for pos in open_positions:
                if pos.get("symbol") == symbol:
                    return True, f"{symbol} 已在持仓中"

        return False, ""

    def get_blocked_strategies(self, regime: str) -> list[dict[str, Any]]:
        """查询在指定 regime 下应暂停的策略列表。

        Returns:
            [{"strategy_id": str, "win_rate": float, "reason": str}, ...]
        """
        stats = self.strategy_ledger.get_all_stats(regime=regime)
        blocked = []
        for s in stats:
            if s["recommendation"] == "SUSPEND":
                blocked.append({
                    "strategy_id": s["strategy_id"],
                    "win_rate": s["win_rate"],
                    "total_trades": s["total_trades"],
                    "reason": (
                        f"{s['strategy_id']} 在 {regime} 环境下 "
                        f"胜率仅 {s['win_rate']:.0%} "
                        f"({s['wins']}胜/{s['losses']}负)"
                    ),
                })
        return blocked

    def filter_candidates(
        self,
        candidates: list[dict],
        regime: str | None = None,
    ) -> list[dict]:
        """对候选票列表应用黑名单过滤。

        - 冷却期内的个股 → 直接移除
        - 历史胜率极低的个股 → 降权 (score × 0.5)
        - 已持仓的个股 → 直接移除

        Returns:
            过滤后的候选票列表（可能减少）
        """
        filtered = []
        blocked_count = 0

        for c in candidates:
            symbol = c.get("symbol", "")

            # 黑名单检查
            is_blocked, reason = self.is_blocked(symbol, regime=regime)
            if is_blocked:
                logger.info("黑名单过滤: %s — %s", symbol, reason)
                blocked_count += 1
                continue

            # 历史胜率降权（不移除，给 Strategist 一个机会）
            history = self.stock_memory.get_history(symbol)
            if history and history["trade_count"] >= 3 and history["win_rate"] < 0.3:
                original_score = c.get("qlib_score", 1.0)
                c["qlib_score"] = round(original_score * 0.5, 3)
                c.setdefault("hot_reason", []).append(
                    f"历史胜率低 ({history['win_rate']:.0%}，{history['trade_count']}次)，已降权"
                )
                logger.info(
                    "经验降权: %s 胜率%.0f%% score %.3f→%.3f",
                    symbol, history["win_rate"] * 100,
                    original_score, c["qlib_score"],
                )

            filtered.append(c)

        if blocked_count > 0:
            logger.info("黑名单过滤移除 %d 只候选", blocked_count)

        # 按分数重排
        return sorted(filtered, key=lambda x: x.get("qlib_score", 0), reverse=True)
