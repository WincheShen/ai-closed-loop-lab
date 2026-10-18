"""DataStep / Factor 注册表 — 配置按 name 解析到实现。"""
from __future__ import annotations

from typing import Any

_DATA_STEPS: dict[str, Any] = {}
_FACTORS: dict[str, Any] = {}
_builtins_ready = False


def register_data_step(step: Any) -> None:
    if not getattr(step, "name", None):
        raise ValueError("DataStep 必须有 name")
    _DATA_STEPS[step.name] = step


def register_factor(factor: Any) -> None:
    if not getattr(factor, "name", None):
        raise ValueError("Factor 必须有 name")
    if getattr(factor, "stage", None) not in ("veto", "propose", "modify", "screen"):
        raise ValueError(f"Factor {factor.name} stage 非法: {factor.stage}")
    _FACTORS[factor.name] = factor


def data_step(name: str) -> Any:
    ensure_builtins_registered()
    return _DATA_STEPS[name]


def factor(name: str) -> Any:
    ensure_builtins_registered()
    return _FACTORS[name]


def data_step_names() -> set[str]:
    ensure_builtins_registered()
    return set(_DATA_STEPS)


def factor_names() -> set[str]:
    ensure_builtins_registered()
    return set(_FACTORS)


def has(name: str) -> bool:
    ensure_builtins_registered()
    return name in _DATA_STEPS or name in _FACTORS


def ensure_builtins_registered() -> None:
    """惰性导入内置实现，避免循环依赖。"""
    global _builtins_ready
    if _builtins_ready:
        return
    from src.wheel import data as _data  # noqa: F401
    from src.wheel import factors as _factors  # noqa: F401
    _builtins_ready = True


def reset_for_tests() -> None:
    global _builtins_ready
    _DATA_STEPS.clear()
    _FACTORS.clear()
    _builtins_ready = False
