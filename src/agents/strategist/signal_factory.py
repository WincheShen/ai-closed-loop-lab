"""SignalFactory — 检测器出策略/价格，LLM 只评 TAKE/PASS。

开关：SIGNAL_FACTORY=1 才走本模块。未过 G-bt-* 前保持关闭。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any, Callable

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

FACTORY_REVIEW_PROMPT = """\
你只评审一条已经算好价格的交易候选。
禁止改 strategy / entry_price / stop_loss / target_price / entry_condition。
只能输出 JSON: {"decision":"TAKE"|"PASS","confidence":0-1,"rationale":"..."}
PASS 条件：逻辑与当日 regime/posture 明显冲突，或 evidence 自相矛盾。
不确定时 TAKE（价格已由规则算出，你不是定价人）。
"""


def signal_factory_enabled() -> bool:
    return os.getenv("SIGNAL_FACTORY", "0").strip() in {"1", "true", "TRUE", "yes"}


def allowed_strategy_ids(persona) -> list[str]:
    compat = getattr(persona, "strategy_regime_compatibility", None) or {}
    if isinstance(compat, dict) and compat:
        ids = [sid for sid in compat if sid in DETECTORS]
        if ids:
            return ids
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
    regime = dict(market_regime or {})
    hot = tuple(hot_sectors or regime.get("hot_sectors") or [])
    if hot and not regime.get("hot_sectors"):
        regime["hot_sectors"] = list(hot)
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
    """价格/策略只从 DetectorHit 拷贝。"""
    out = []
    for cand, hit in hits:
        out.append(hit_to_signal(cand, hit))
    return out


def hit_to_signal(cand: dict, hit: DetectorHit, *, rationale: str = "", confidence: float = 0.5) -> dict[str, Any]:
    return {
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
        "rationale": rationale or str(hit.evidence),
        "timestamp": datetime.now().isoformat(),
        "sector": cand.get("sector", ""),
        "confidence": confidence,
        "current_price": hit.entry_price,
    }


def _parse_decision(content: str) -> dict[str, Any]:
    import json
    text = (content or "").strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        l, r = text.find("{"), text.rfind("}")
        if l >= 0 and r > l:
            data = json.loads(text[l : r + 1])
        else:
            return {"decision": "TAKE", "confidence": 0.4, "rationale": "parse_fail"}
    decision = str(data.get("decision") or "TAKE").upper()
    if decision not in {"TAKE", "PASS"}:
        decision = "TAKE"
    return {
        "decision": decision,
        "confidence": float(data.get("confidence") or 0.5),
        "rationale": str(data.get("rationale") or ""),
    }


def review_hits(
    hits: list[tuple[dict, DetectorHit]],
    *,
    market_regime: dict | None,
    llm_invoke: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """有 hit 才可能调 LLM；LLM 改价/改策略一律丢弃。"""
    if not hits:
        return []
    regime = market_regime or {}
    if llm_invoke is None:
        from src.infra.model_adapter import get_llm
        llm_invoke = get_llm().invoke
    signals: list[dict[str, Any]] = []
    for cand, hit in hits:
        user = (
            f"symbol={cand.get('symbol')} name={cand.get('name')} sector={cand.get('sector')}\n"
            f"regime={regime.get('regime')} posture={regime.get('recommended_posture')}\n"
            f"strategy={hit.strategy_id} entry={hit.entry_price} stop={hit.stop_loss} "
            f"target={hit.target_price} cond={hit.entry_condition}\n"
            f"evidence={hit.evidence}"
        )
        try:
            resp = llm_invoke([
                {"role": "system", "content": FACTORY_REVIEW_PROMPT},
                {"role": "user", "content": user},
            ])
            content = getattr(resp, "content", resp)
            verdict = _parse_decision(str(content))
        except Exception as exc:
            logger.warning("factory LLM 失败，默认 TAKE: %s", exc)
            verdict = {"decision": "TAKE", "confidence": 0.4, "rationale": "llm_error"}
        if verdict["decision"] == "PASS":
            logger.info("[SignalFactory] PASS %s %s", cand.get("symbol"), hit.strategy_id)
            continue
        signals.append(
            hit_to_signal(
                cand, hit,
                rationale=verdict.get("rationale") or str(hit.evidence),
                confidence=float(verdict.get("confidence") or 0.5),
            )
        )
    return signals


def run_factory(
    candidates: list[dict],
    *,
    persona,
    market_regime: dict | None,
    hot_sectors: list[str] | None = None,
    max_hits: int = 8,
    llm_invoke: Callable[..., Any] | None = None,
) -> tuple[list[tuple[dict, DetectorHit]], list[dict[str, Any]]]:
    hits = detect_hits(
        candidates, persona=persona, market_regime=market_regime, hot_sectors=hot_sectors,
    )
    trimmed = hits[:max_hits]
    if not trimmed:
        return hits, []
    return hits, review_hits(trimmed, market_regime=market_regime, llm_invoke=llm_invoke)
