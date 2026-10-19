"""内置 DataStep 注册入口。"""
from __future__ import annotations

from src.wheel import registry
from src.wheel.data import steps

steps.register(registry)
