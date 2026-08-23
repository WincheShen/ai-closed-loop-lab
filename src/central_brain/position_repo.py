"""positions / fills / orders mixin 占位 — 方法仍在 MemoryStore。"""
from __future__ import annotations


class PositionRepoMixin:
    """调用面仍走 MemoryStore.list_open_positions 等现有方法。"""
