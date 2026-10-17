"""内置 Factor 注册入口。"""
from __future__ import annotations

from src.wheel import registry
from src.wheel.factors import exit_rules, floor_factors, llm_factors, modifiers

exit_rules.register(registry)
floor_factors.register(registry)
llm_factors.register(registry)
modifiers.register(registry)
