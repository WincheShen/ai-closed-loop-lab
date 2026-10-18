"""影子模式接入 — 旧路径照旧执行，转盘并行写 shadow 决策。

Phase 1 约定：
- hard_scan 每 5 分钟：对每个持仓并行跑出场转盘（mode=shadow）；
- intraday_review：对每个 LLM 复审结果并行跑 llm_position_review 转盘；
- force_close：对每个隔夜持仓并行跑 overnight_force_close 转盘；
- 16:00 health_check：对比当日 shadow 决策与实际动作，输出对比报告。

任何影子异常都不得影响旧路径：所有入口都有兜底 try/except。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from src.wheel.protocol import GATE_WHEELS, Decision, WheelEvent

logger = logging.getLogger(__name__)

# 门槛报告只统计 GATE_WHEELS（Phase 1 出场转盘）；Phase 2 起新转盘
# （morning_watch / urgent_review 等）的影子决策单列 other_wheels 观察。

DEFAULT_PERSONA = "short_term_hot_rotation_v1"


def _make_event(
    event_type: str, persona_id: str, position: dict, source: str, now: datetime,
) -> WheelEvent:
    stamp = now.strftime("%Y%m%d-%H%M%S")
    pid = position.get("position_id") or position.get("symbol") or "unknown"
    return WheelEvent(
        event_id=f"wev-{event_type}-{stamp}-{pid}",
        type=event_type,
        due_at=now,
        persona_id=persona_id,
        subject={"position_id": pid, "symbol": position.get("symbol", "")},
        source=source,
    )


def wheel_exit_mode(persona_id: str | None, event_type: str) -> str:
    """该 persona 在该事件上的出场模式：live / shadow / off（未绑定或异常 → off）。

    live 时旧路径必须让位 — 同一持仓只有一个出场执行方（ExitGateway）。
    """
    try:
        from src.wheel.config import get_config
        binding = get_config().binding_for(persona_id or DEFAULT_PERSONA, event_type)
        return binding.mode if binding else "off"
    except Exception:  # noqa: BLE001
        return "off"


def run_exit_shadow(
    brain,
    position: dict,
    *,
    price: float,
    is_mock: bool,
    event_type: str,
    source: str,
    extra: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Decision | None:
    """对一个持仓跑出场转盘（shadow/live 取决于绑定 mode）。

    返回最终 Decision；绑定缺失/关闭或任何异常 → None（不影响旧路径）。
    """
    try:
        from src.wheel.config import get_config
        from src.wheel.persona import persona_policy
        from src.wheel.runner import run_binding

        now = now or datetime.now()
        persona_id = position.get("persona_id") or DEFAULT_PERSONA
        cfg = get_config()
        binding = cfg.binding_for(persona_id, event_type)
        if not binding or binding.mode == "off":
            return None

        event = _make_event(event_type, persona_id, position, source, now)
        persona = persona_policy(cfg, persona_id, binding)
        preloaded = {"quote": {"price": float(price or 0), "is_mock": bool(is_mock)}}
        if extra:
            preloaded.update(extra)

        results = run_binding(
            cfg, binding, event, persona, brain, preloaded=preloaded, now=now,
        )
        for r in results:
            if r.decisions:
                return r.decisions[0]
        return None
    except Exception:  # noqa: BLE001
        logger.warning(
            "影子转盘执行失败 %s %s", position.get("symbol"), event_type, exc_info=True,
        )
        return None


# ----------------------------------------------------------------------
# 16:00 影子对比报告（Phase 1.6）
# ----------------------------------------------------------------------

_SELL_ACTIONS = ("EXIT", "REDUCE")
_STRENGTH = {"EXIT": 3, "REDUCE": 2, "ADD": 1, "BUY": 1, "HOLD": 0, "SKIP": 0}


def daily_shadow_report(brain, day: str | None = None) -> dict:
    """对比当日 shadow 决策与旧路径实际动作。

    - agree：转盘与旧路径同为卖出类动作
    - wheel_stricter：旧路径卖了/提议卖，转盘 HOLD（需逐条确认是否为预期）
    - wheel_looser：旧路径没动作，转盘提议卖（切换前必须为 0 或逐条解释）

    旧路径依据：当日平仓的 positions + 当日 sell fills（按 symbol+persona 映射回持仓）
    + position_reviews 当日非 HOLD 动作（LLM 复审提议）。
    """
    day = day or date.today().isoformat()
    store = brain.store

    all_decisions = store.list_wheel_decisions(day=day, mode="shadow")
    # 门槛统计只含 Phase 1 出场转盘；其余转盘的影子决策单独计数展示
    decisions = [d for d in all_decisions if d["wheel"] in GATE_WHEELS]
    other_wheels: dict[str, int] = {}
    for d in all_decisions:
        if d["wheel"] not in GATE_WHEELS:
            other_wheels[d["wheel"]] = other_wheels.get(d["wheel"], 0) + 1
    wheel_by_pos: dict[str, dict] = {}
    for d in decisions:
        cur = wheel_by_pos.get(d["subject_key"])
        if cur is None or _STRENGTH.get(d["action"], 0) > _STRENGTH.get(cur["action"], 0):
            wheel_by_pos[d["subject_key"]] = d

    # 持仓索引：fill → position_id。同一 (persona, symbol) 会反复交易
    # （隔夜客常重复做同一批动量股；短线止损后当日可能重新买入）：
    # 1) 当日平仓且 filled_at <= closed_at 的持仓（这笔卖出就是它的平仓单）
    # 2) 未平仓持仓
    # 3) 成交日落在 [entry_date, closed_at] 窗口内的已平仓持仓，取最近一笔
    open_by_key: dict[tuple[str | None, str], str] = {}
    closed_positions: list[dict] = []
    for pos in store.list_positions():
        key = (pos.get("persona_id"), pos["symbol"])
        if pos.get("status") == "open":
            open_by_key.setdefault(key, pos["position_id"])
        else:
            closed_positions.append(pos)

    def _position_for_fill(fill: dict) -> str | None:
        key = (fill.get("persona_id"), fill.get("symbol"))
        filled_at = str(fill.get("filled_at") or "")
        fday = filled_at[:10]
        closing = [
            p for p in closed_positions
            if (p.get("persona_id"), p.get("symbol")) == key
            and str(p.get("closed_at") or "")[:10] == fday
            and filled_at <= str(p.get("closed_at") or "")
        ]
        if closing:
            return min(closing, key=lambda p: str(p.get("closed_at") or ""))["position_id"]
        if key in open_by_key:
            return open_by_key[key]
        best: dict | None = None
        for pos in closed_positions:
            if (pos.get("persona_id"), pos.get("symbol")) != key:
                continue
            entry_day = str(pos.get("entry_date") or "")[:10]
            closed_day = str(pos.get("closed_at") or "")[:10]
            if not entry_day or fday < entry_day or (closed_day and fday > closed_day):
                continue
            if best is None or entry_day > str(best.get("entry_date") or "")[:10]:
                best = pos
        return best["position_id"] if best else None

    legacy: dict[str, dict] = {}
    # 当日实际平仓 → legacy EXIT
    for pos in store.list_positions(status="closed"):
        if (pos.get("closed_at") or "").startswith(day):
            legacy[pos["position_id"]] = {"action": "EXIT", "source": "closed"}
    # 当日 sell fills → 按 (persona, symbol) 映射回持仓（覆盖 REDUCE 未平仓）
    for fill in store.list_sell_fills(day):
        pid = _position_for_fill(fill)
        if pid:
            legacy.setdefault(pid, {"action": "REDUCE", "source": "fill"})
    # LLM 复审提议（含未执行的）
    for row in store.list_position_reviews_today(day):
        if row.get("action") == "HOLD":
            continue
        cur = legacy.get(row["position_id"])
        if cur is None or _STRENGTH.get(row["action"], 0) > _STRENGTH.get(cur["action"], 0):
            legacy[row["position_id"]] = dict(row)

    agree, stricter, looser, uncovered, gw_rejected = [], [], [], [], []
    for pid, wd in wheel_by_pos.items():
        leg = legacy.get(pid)
        gateway = str(wd.get("gateway") or "")
        proposed_sell = wd["action"] in _SELL_ACTIONS
        wheel_sells = proposed_sell and not gateway.startswith("rejected")
        leg_sells = bool(leg) and leg.get("action") in _SELL_ACTIONS
        item = {
            "position_id": pid,
            "wheel": {
                "wheel": wd["wheel"], "action": wd["action"],
                "decided_by": wd.get("decided_by", ""),
                "reason": wd.get("reason", ""),
            },
            "gateway": gateway or None,
            "legacy": {"action": leg.get("action") if leg else None},
        }
        if proposed_sell and gateway.startswith("rejected"):
            gw_rejected.append(item)
        elif wheel_sells and leg_sells:
            agree.append(item)
        elif leg_sells and not wheel_sells:
            stricter.append(item)
        elif wheel_sells and not leg_sells:
            looser.append(item)

    # 覆盖缺口：旧路径卖了、但转盘当天没评估过该持仓 — 单独列出，
    # 不计入更严/更松，但必须逐条解释后才能切 live
    for pid, leg in legacy.items():
        if pid in wheel_by_pos or leg.get("action") not in _SELL_ACTIONS:
            continue
        uncovered.append({
            "position_id": pid,
            "legacy": {"action": leg.get("action"), "source": leg.get("source")},
        })

    report = {
        "day": day,
        "wheel_decisions": len(decisions),
        "agree": len(agree),
        "wheel_stricter": len(stricter),
        "wheel_looser": len(looser),
        "gateway_rejected": len(gw_rejected),
        "uncovered": len(uncovered),
        "other_wheels": other_wheels,
        "stricter_items": stricter,
        "looser_items": looser,
        "uncovered_items": uncovered,
    }

    logger.info(
        "[WheelShadow] %s 决策=%d | 一致=%d | 转盘更严=%d | 转盘更松=%d | "
        "网关拒绝=%d | 未覆盖=%d",
        day, len(decisions), len(agree), len(stricter), len(looser),
        len(gw_rejected), len(uncovered),
    )
    for item in stricter:
        logger.info(
            "[WheelShadow] 更严 %s: legacy=%s → wheel=%s",
            item["position_id"], item["legacy"]["action"], item["wheel"]["action"],
        )
    for item in looser:
        logger.warning(
            "[WheelShadow] 更松 %s: legacy=%s → wheel=%s (%s)",
            item["position_id"], item["legacy"]["action"],
            item["wheel"]["action"], item["wheel"]["reason"],
        )
    for item in uncovered:
        logger.warning(
            "[WheelShadow] 未覆盖 %s: legacy=%s (%s) 但转盘当天无该持仓决策",
            item["position_id"], item["legacy"]["action"], item["legacy"].get("source"),
        )

    if hasattr(brain, "log_agent_event"):
        try:
            brain.log_agent_event(
                session_id=f"wheel-shadow-{day}",
                agent="wheel",
                event_type="wheel_shadow_report",
                payload=report,
            )
        except Exception:  # noqa: BLE001
            logger.debug("wheel_shadow_report 事件写入失败", exc_info=True)

    return report
