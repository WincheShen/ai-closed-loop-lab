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

    def shared_stock_flags(self, symbol: str) -> list[dict]:
        """跨角色共享的客观事实标记（如退市风险、财务造假、重大违规，§7.4）。"""
        key = f"shared_flags:{symbol}"
        if key in self._cache:
            return self._cache[key]
        if self._brain is None:
            return []
        as_of_iso = self.as_of.isoformat()
        try:
            rows = self._brain.store.get_shared_stock_flags(symbol)
            res = [r for r in rows if (r.get("created_at") or "") <= as_of_iso]
        except Exception:  # noqa: BLE001
            res = []
        self._cache[key] = res
        return res


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
    kind = pc.kind if pc else fallback_kind
    bparams = dict(binding.params) if binding else {}
    meta = _persona_meta(pid, kind)
    return PersonaPolicy(
        persona_id=pid,
        kind=kind,
        params=bparams,
        allowed_strategies=meta["allowed_strategies"],
        llm_review=_llm_review_allowed(pid),
        daily_pipeline=meta["daily_pipeline"],
        max_daily_loss_pct=float(bparams.get("max_daily_loss_pct", meta["max_daily_loss_pct"])),
        consecutive_loss_limit=int(bparams.get("consecutive_loss_limit", meta["consecutive_loss_limit"])),
        consecutive_loss_cooldown_days=int(
            bparams.get("consecutive_loss_cooldown_days", meta["consecutive_loss_cooldown_days"])
        ),
    )


def _persona_meta(persona_id: str, kind: str) -> dict[str, Any]:
    """从 trading_persona.yaml 提取角色风控阈值与允许策略白名单（Phase 3.5 / Phase 4）。"""
    allowed: tuple[str, ...] = ("ultra_short_momentum",) if kind == "ultra" else ()
    daily_pipeline = kind != "ultra"
    max_daily_loss_pct = 0.02 if kind == "ultra" else (0.05 if kind == "value" else 0.03)
    consecutive_loss_limit = 2 if kind == "ultra" else 5
    consecutive_loss_cooldown_days = 1
    try:
        from src.agents.cio.trading_persona import list_personas, load_persona_by_id
        for raw in list_personas():
            if raw.get("id") == persona_id:
                daily_pipeline = bool(raw.get("daily_pipeline", daily_pipeline))
                break
        p = load_persona_by_id(persona_id)
        if p is not None and p.id == persona_id:
            max_daily_loss_pct = float(p.max_daily_loss_pct)
            consecutive_loss_limit = int(p.consecutive_loss_limit)
            consecutive_loss_cooldown_days = int(
                p.risk_limits.get("consecutive_loss_cooldown_days", 1)
            )
            if kind == "ultra" and p.strategy_regime_compatibility:
                allowed = tuple(p.strategy_regime_compatibility.keys())
    except Exception:  # noqa: BLE001
        pass
    return {
        "allowed_strategies": allowed,
        "daily_pipeline": daily_pipeline,
        "max_daily_loss_pct": max_daily_loss_pct,
        "consecutive_loss_limit": consecutive_loss_limit,
        "consecutive_loss_cooldown_days": consecutive_loss_cooldown_days,
    }


def compute_account_risk_metrics(
    brain: Any,
    persona_id: str,
    *,
    now: datetime | None = None,
    window_days: int = 14,
    max_daily_loss_pct: float | None = None,
    consecutive_loss_limit: int | None = None,
    cooldown_days: int | None = None,
    emit_events: bool = False,
) -> dict[str, Any]:
    """统一计算账户当日盈亏与连续亏损笔数（Phase 4，ExecutionEngine 与 account_monitor 共用）。

    连亏语义 = "冷静/降频"：consecutive_losses 达到上限后，仅当最近一笔亏损平仓
    距今 <= cooldown_days 个交易日时才拦截新开仓；冷静期过后允许恢复交易，
    避免"连亏锁死 14 天且无法产生盈利单打破连亏"的死锁。
    """
    now_dt = now or datetime.now()
    today = now_dt.strftime("%Y-%m-%d")
    window_start = (now_dt - timedelta(days=window_days)).isoformat()

    if max_daily_loss_pct is None or consecutive_loss_limit is None or cooldown_days is None:
        try:
            from src.wheel.config import get_config
            pol = persona_policy(get_config(), persona_id, None)
            if max_daily_loss_pct is None:
                max_daily_loss_pct = pol.max_daily_loss_pct
            if consecutive_loss_limit is None:
                consecutive_loss_limit = pol.consecutive_loss_limit
            if cooldown_days is None:
                cooldown_days = int(pol.consecutive_loss_cooldown_days)
        except Exception:  # noqa: BLE001
            max_daily_loss_pct = max_daily_loss_pct or 0.03
            consecutive_loss_limit = consecutive_loss_limit or 5
            cooldown_days = cooldown_days or 1

    max_daily_loss_pct = abs(float(max_daily_loss_pct))
    consecutive_loss_limit = int(consecutive_loss_limit)

    closed_positions: list[dict] = []
    open_positions: list[dict] = []
    account = None
    if brain is not None:
        try:
            closed_positions = brain.store.list_positions(persona_id=persona_id, status="closed")
            open_positions = brain.store.list_open_positions(persona_id=persona_id)
            account = brain.store.get_account_by_persona(persona_id)
        except Exception:  # noqa: BLE001
            pass

    closed_today = [
        p for p in closed_positions
        if (p.get("closed_at") or "").startswith(today)
    ]
    daily_pnl = sum(float(p.get("realized_pnl") or 0.0) for p in closed_today)

    capital = 300000.0
    if account:
        capital = float(account.get("available_cash") or 0.0) + sum(
            float(p.get("entry_price") or 0.0) * int(p.get("current_qty") or 0)
            for p in open_positions
        )
        if capital <= 0:
            capital = float(account.get("capital") or 300000.0)

    daily_limit_amount = -capital * max_daily_loss_pct
    daily_loss_breached = daily_pnl < daily_limit_amount

    recent_in_window = [
        p for p in closed_positions
        if (p.get("closed_at") or "") >= window_start
    ]
    recent = sorted(recent_in_window, key=lambda p: p.get("closed_at") or "", reverse=True)
    consecutive_losses = 0
    for p in recent[:consecutive_loss_limit + 2]:
        if float(p.get("realized_pnl") or 0.0) < 0:
            consecutive_losses += 1
        else:
            break

    # 连亏冷静期：只有"最近一笔亏损仍在 cooldown_days 个交易日内"才拦截；
    # 冷静期过后连亏计数保留在指标中，但不再拦截新开仓
    streak_active = False
    days_since_last_loss: int | None = None
    if consecutive_losses > 0 and recent:
        try:
            from src.infra.trading_calendar import trading_days_between
            last_closed = str(recent[0].get("closed_at") or "")[:10]
            days_since_last_loss = trading_days_between(
                datetime.fromisoformat(last_closed).date(), now_dt.date(),
            )
            streak_active = (
                consecutive_losses >= consecutive_loss_limit
                and days_since_last_loss <= int(cooldown_days)
            )
        except (ValueError, TypeError):
            streak_active = consecutive_losses >= consecutive_loss_limit

    consecutive_loss_breached = consecutive_losses >= consecutive_loss_limit and streak_active

    if emit_events and brain is not None:
        try:
            if daily_loss_breached:
                brain.store.insert_wheel_event({
                    "event_id": f"account.daily_loss_breach|{persona_id}|{today}",
                    "type": "account.daily_loss_breach",
                    "persona_id": persona_id,
                    "subject": {"account": persona_id},
                    "payload": {
                        "daily_pnl": round(daily_pnl, 2),
                        "daily_limit": round(daily_limit_amount, 2),
                        "max_daily_loss_pct": max_daily_loss_pct,
                    },
                    "source": "account_monitor",
                    "due_at": now_dt.isoformat(),
                })
            if consecutive_loss_breached:
                brain.store.insert_wheel_event({
                    "event_id": f"account.consecutive_losses|{persona_id}|{today}|{consecutive_losses}",
                    "type": "account.consecutive_losses",
                    "persona_id": persona_id,
                    "subject": {"account": persona_id},
                    "payload": {
                        "consecutive_losses": consecutive_losses,
                        "consecutive_loss_limit": consecutive_loss_limit,
                        "window_days": window_days,
                    },
                    "source": "account_monitor",
                    "due_at": now_dt.isoformat(),
                })
        except Exception:  # noqa: BLE001
            pass

    return {
        "persona_id": persona_id,
        "capital": round(capital, 2),
        "daily_pnl": round(daily_pnl, 2),
        "daily_limit": round(daily_limit_amount, 2),
        "max_daily_loss_pct": max_daily_loss_pct,
        "daily_loss_breached": daily_loss_breached,
        "consecutive_losses": consecutive_losses,
        "consecutive_loss_limit": consecutive_loss_limit,
        "consecutive_loss_breached": consecutive_loss_breached,
        "streak_active": streak_active,
        "days_since_last_loss": days_since_last_loss,
        "cooldown_days": int(cooldown_days),
        "window_days": window_days,
    }


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
