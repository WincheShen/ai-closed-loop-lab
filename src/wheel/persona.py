"""角色档案装配（最小实现 — Phase 1 只用行为配置层）。

完整的 PersonaProfile（身份 + 行为配置 + 记忆视图，as_of 截取）
在 Phase 3.6 落地；本模块只负责把 wheels.yaml 的角色段落转成
转盘可用的 PersonaPolicy。
"""
from __future__ import annotations

from src.wheel.config import Binding, WheelConfig
from src.wheel.protocol import PersonaPolicy


def persona_policy(
    cfg: WheelConfig,
    persona_id: str | None,
    binding: Binding | None,
    *,
    fallback_kind: str = "short",
) -> PersonaPolicy:
    """装配当前绑定下的角色策略快照（只读）。"""
    pid = persona_id or ""
    pc = cfg.personas.get(pid)
    return PersonaPolicy(
        persona_id=pid,
        kind=(pc.kind if pc else fallback_kind),
        params=dict(binding.params) if binding else {},
        llm_review=_llm_review_allowed(pid),
    )


def _llm_review_allowed(persona_id: str) -> bool:
    try:
        from src.agents.cio.trading_persona import persona_allows_llm_review
        return persona_allows_llm_review(persona_id)
    except Exception:  # noqa: BLE001
        return True
