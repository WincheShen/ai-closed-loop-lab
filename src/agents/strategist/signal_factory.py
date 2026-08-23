"""SignalFactory — 规则检测器产出候选，LLM 不再发明策略/价格。

开关：环境变量 SIGNAL_FACTORY=1 时由 run_strategy_node 走本模块。
未开时保持旧 Strategist 自由生成（两周过渡）。
"""
from __future__ import annotations

import os
from typing import Any

from src.stock_analyzer.data_source.akshare_client import KlineBar
from src.stock_analyzer.detectors import DETECTORS, BarContext, DetectorHit
from src.infra.data_quality import strategy_blocked_by_quality
from src.infra.logger import get_agent_logger

logger = get_agent_logger("signal_factory", "init")

DEFAULT_ALLOWED = (
    "hot_sector_pullback",
    "bottom_reversal",
    "value_investing",
)


def signal_factory_enabled() -> bool:
    return os.getenv("SIGNAL_FACTORY", "0").strip() in {"1", "true", "TRUE", "yes"}


def allowed_strategy_ids(persona) -> list[str]:
    compat = getattr(persona, "strategy_regime_compatibility", None) or {}
    if isinstance(compat, dict) and compat:
        return [sid for sid in compat if sid in DETECTORS]
    return [s for s in DEFAULT_ALLOWED if s in DETECTORS]


def bars_from_candidate(candidate: dict) -> list[KlineBar]:
    raw = (candidate.get("kline_summary") or {}).get("bars") or []
    out: list[KlineBar] = []
    for b in raw:
        if isinstance(b, KlineBar):
            out.append(b)
            continue
        if not isinstance(b, dict):
            continue
        try:
            from datetime import date as date_cls
            d = b.get("date")
            if isinstance(d, str):
                d = date_cls.fromisoformat(d[:10])
            out.append(
                KlineBar(
                    date=d,
                    open=float(b["open"]),
                    high=float(b["high"]),
                    low=float(b["low"]),
                    close=float(b["close"]),
                    volume=float(b.get("volume") or 0),
                    turnover=float(b.get("turnover") or 0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def detect_hits(
    candidates: list[dict],
    *,
    persona,
    market_regime: dict | None,
    hot_sectors: list[str] | None = None,
) -> list[tuple[dict, DetectorHit]]:
    regime = market_regime or {}
    hot = tuple(hot_sectors or regime.get("hot_sectors") or [])
    allowed = allowed_strategy_ids(persona)
    hits: list[tuple[dict, DetectorHit]] = []
    for cand in candidates:
        bars = bars_from_candidate(cand)
        if len(bars) < 12:
            continue
        ctx = BarContext(
            symbol=cand.get("symbol", ""),
            bars=bars,
            sector=cand.get("sector") or "",
            hot_sectors=hot,
            inst_net_buy_wan=float((cand.get("dragon_tiger") or {}).get("inst_net_buy_wan") or 0),
            pe_ttm=float((cand.get("kline_summary") or {}).get("pe_ttm") or 0),
        )
        for sid in allowed:
            if strategy_blocked_by_quality(sid, regime):
                continue
            fn = DETECTORS.get(sid)
            if not fn:
                continue
            hit = fn(ctx)
            if hit:
                hits.append((cand, hit))
    return hits


def hits_to_signals(hits: list[tuple[dict, DetectorHit]]) -> list[dict[str, Any]]:
    """不经 LLM，直接把 hit 变成可测的 TradeSignal 骨架。"""
    import uuid
    from datetime import datetime

    out = []
    for cand, hit in hits:
        out.append({
            "signal_id": f"SIG-FAC-{uuid.uuid4().hex[:8].upper()}",
            "symbol": cand.get("symbol"),
            "name": cand.get("name", ""),
            "action": "buy",
            "entry_price": hit.entry_price,
            "target_price": hit.target_price,
            "stop_loss": hit.stop_loss,
            "position_pct": hit.position_pct_cap,
            "strategy": hit.strategy_id,
            "strategy_id": hit.strategy_id,
            "entry_condition": hit.entry_condition,
            "rationale": str(hit.evidence),
            "timestamp": datetime.now().isoformat(),
            "sector": cand.get("sector", ""),
            "confidence": 0.5,
        })
    return out
