"""Experience Layer — 交易经验的结构化存储与查询。

核心模块：
- StrategyLedger: 策略×regime 胜率台账（替代 prompt_weights.json）
- StockMemory:    个股交易记忆（冷却期、历史胜率）
- DynamicBlacklist: 自动黑名单（连续止损 → 冷却）

所有数据基于 trade_attributions 表实时聚合，单一事实来源，不再维护独立的 JSON 文件。
"""

from src.experience_layer.strategy_ledger import StrategyLedger
from src.experience_layer.stock_memory import StockMemory
from src.experience_layer.dynamic_blacklist import DynamicBlacklist

__all__ = ["StrategyLedger", "StockMemory", "DynamicBlacklist", "get_experience"]

# ── Singleton ──────────────────────────────────────────────────────────────

_experience: "ExperienceAPI | None" = None


def get_experience() -> "ExperienceAPI":
    """获取 ExperienceLayer 单例。"""
    global _experience
    if _experience is None:
        from src.experience_layer.experience_api import ExperienceAPI
        _experience = ExperienceAPI()
    return _experience
