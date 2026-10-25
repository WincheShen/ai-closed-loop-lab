"""角色档案装配（Phase 3.6 — Identity + PersonaPolicy + as_of 惰性记忆视图）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from src.wheel.config import Binding, WheelConfig
from src.wheel.protocol import PersonaPolicy


@dataclass(frozen=True)
class Identity:
    """来自 config/trading_persona.yaml 的只读身份层（§7.2）。"""

    persona_id: str
    name: str = ""
    philosophy: str = ""
    holding_period: tuple[int, ...] = ()
    risk_appetite: str = "medium"
    capital: float = 300000.0
    allowed_strategies: tuple[str, ...] = ()


class MemoryViews:
    """按 as_of 截取且按 persona_id 隔离的惰性只读记忆视图（§7.3 / §7.4）。"""

    def __init__(self, brain: Any, persona_id: str, as_of: datetime) -> None:
        self._brain = brain
        self.persona_id = persona_id
        self.as_of = as_of
        self._cache: dict[str, Any] = {}

    def trade_history(self, days: int = 180) -> list[dict]:
        key = f"trade_history:{days}"
        if key in self._cache:
            return self._cache[key]
        if self._brain is None:
            return []
        cutoff = (self.as_of - timedelta(days=days)).date().isoformat()
        as_of_iso = self.as_of.isoformat()
        rows = [
            p for p in self._brain.store.list_positions(persona_id=self.persona_id)
            if (p.get("entry_date") or "") >= cutoff
            and (p.get("created_at") or "") <= as_of_iso
        ]
        self._cache[key] = rows
        return rows

    def attributions(self, days: int = 180) -> list[dict]:
        key = f"attributions:{days}"
        if key in self._cache:
            return self._cache[key]
        if self._brain is None:
            return []
        cutoff_iso = (self.as_of - timedelta(days=days)).isoformat()
        as_of_iso = self.as_of.isoformat()
        try:
            conn = self._brain.store._conn()
            rows = conn.execute(
                """SELECT ta.* FROM trade_attributions ta
                LEFT JOIN positions p ON p.position_id = ta.position_id
                WHERE (ta.persona_id = ? OR p.persona_id = ?)
                  AND ta.created_at >= ? AND ta.created_at <= ?
                ORDER BY ta.created_at DESC""",
                (self.persona_id, self.persona_id, cutoff_iso, as_of_iso),
            ).fetchall()
            res = [dict(r) for r in rows]
        except Exception:  # noqa: BLE001
            res = []
        self._cache[key] = res
        return res

    def lessons(self, top: int = 10) -> list[dict]:
        key = f"lessons:{top}"
        if key in self._cache:
            return self._cache[key]
        if self._brain is None:
            return []
        as_of_iso = self.as_of.isoformat()
        try:
            conn = self._brain.store._conn()
            rows = conn.execute(
                """SELECT l.* FROM lessons l
                LEFT JOIN trade_attributions ta ON ta.attribution_id = l.attribution_id
                LEFT JOIN positions p ON p.position_id = ta.position_id
                WHERE (l.persona_id = ? OR ta.persona_id = ? OR p.persona_id = ?)
                  AND l.created_at <= ?
                ORDER BY l.relevance_score DESC, l.created_at DESC LIMIT ?""",
                (self.persona_id, self.persona_id, self.persona_id, as_of_iso, int(top)),
            ).fetchall()
            res = [dict(r) for r in rows]
        except Exception:  # noqa: BLE001
            res = []
        self._cache[key] = res
        return res

    def watchlist(self, status: str = "watching") -> list[dict]:
        key = f"watchlist:{status}"
        if key in self._cache:
            return self._cache[key]
        if self._brain is None:
            return []
        as_of_iso = self.as_of.isoformat()
        items = self._brain.store.get_watchlist(status=status)
        filtered = [
            it for it in items
            if (it.get("added_at") or "") <= as_of_iso
            and (it.get("persona_id") in (None, "", self.persona_id))
        ]
        self._cache[key] = filtered
        return filtered


@dataclass(frozen=True)
class PersonaProfile:
    """完整角色档案快照（§7.3）：身份 + 行为配置 + as_of 惰性记忆视图。"""

    persona_id: str
    identity: Identity
    policy: PersonaPolicy
    memory: MemoryViews
    as_of: datetime = field(default_factory=datetime.now)


def load_identity(persona_id: str) -> Identity:
    """从 trading_persona.yaml 加载只读身份层。"""
    try:
        from src.agents.cio.trading_persona import load_persona_by_id
        p = load_persona_by_id(persona_id)
        if p is not None:
            prefer = tuple((p.strategy_preferences or {}).get("prefer") or ())
            return Identity(
                persona_id=persona_id,
                name=getattr(p, "name", persona_id),
                philosophy=getattr(p, "philosophy", ""),
                holding_period=tuple(getattr(p, "preferred_holding_days", ()) or ()),
                risk_appetite=getattr(p, "risk_appetite", "medium"),
                capital=float(getattr(p, "capital", 300000.0) or 300000.0),
                allowed_strategies=prefer,
            )
    except Exception:  # noqa: BLE001
        pass
    return Identity(persona_id=persona_id)


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


def assemble_persona_profile(
    cfg: WheelConfig,
    persona_id: str | None,
    binding: Binding | None,
    brain: Any = None,
    *,
    as_of: datetime | None = None,
) -> PersonaProfile:
    """装配完整 PersonaProfile（身份 + 行为配置 + as_of 记忆视图）。"""
    pid = persona_id or "short_term_hot_rotation_v1"
    now = as_of or datetime.now()
    pol = persona_policy(cfg, pid, binding)
    ident = load_identity(pid)
    mem = MemoryViews(brain, pid, now)
    return PersonaProfile(
        persona_id=pid,
        identity=ident,
        policy=pol,
        memory=mem,
        as_of=now,
    )


def _llm_review_allowed(persona_id: str) -> bool:
    try:
        from src.agents.cio.trading_persona import persona_allows_llm_review
        return persona_allows_llm_review(persona_id)
    except Exception:  # noqa: BLE001
        return True
