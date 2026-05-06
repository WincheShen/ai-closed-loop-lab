"""策略编译与执行模块。

流程：
    自然语言策略 → compiler (LLM) → StrategySpec (结构化)
    StrategySpec → executor (AKShare) → 选股结果
"""
from .compiler import StrategyCompiler, StrategySpec
from .executor import StrategyExecutor, PickResult

__all__ = ["StrategyCompiler", "StrategySpec", "StrategyExecutor", "PickResult"]
