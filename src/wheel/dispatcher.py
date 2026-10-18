"""Dispatcher — 事件层分发器（Phase 2.1）。

职责：
- 15 秒轮询 wheel_events，到期事件按绑定跑转盘（本阶段只接管
  followup.* / market.*，时钟事件仍由 scheduler 直接调用，见 §12 拆分说明）。
- 后续事件约束统一在这里执行：只在交易日/交易时段、午休顺延 13:00、
  超过 until/expires_at 作废、attempt 超上限作废、持仓已平作废、
  同类后续事件同持仓最多 1 条在途、event_id 幂等。
- 单事件执行超时 → failed + notify_ops；进程重启时 running 超时回 due。

Scheduler 里以 daemon 线程运行 dispatcher_loop；任何异常不拖垮调度器。
"""
from __future__ import annotations

import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time
from typing import Any

from src.infra.trading_calendar import is_trading_day
from src.wheel.protocol import FollowUp, WheelEvent

logger = logging.getLogger(__name__)

POLL_SECONDS = 15
EXEC_TIMEOUT_S = 60          # 单事件执行超时（本阶段无 LLM 转盘）
RECOVER_AFTER_S = 300        # running 超过该时长视为僵尸，回退 due

_MORNING = (time(9, 30), time(11, 30))
_AFTERNOON = (time(13, 0), time(15, 0))
_HANDLED_PREFIXES = ("followup.", "market.", "account.", "position.")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wheel-dispatch")


def _in_session(t: time) -> bool:
    return _MORNING[0] <= t <= _MORNING[1] or _AFTERNOON[0] <= t <= _AFTERNOON[1]


def _next_session_start(now: datetime) -> datetime:
    """同一交易日内最近的交易时段起点（跨午休顺延到 13:00）。"""
    t = now.time()
    if t < _MORNING[0]:
        return now.replace(hour=_MORNING[0].hour, minute=_MORNING[0].minute, second=0)
    return now.replace(hour=_AFTERNOON[0].hour, minute=_AFTERNOON[0].minute, second=0)


# ----------------------------------------------------------------------
# 后续事件落库（runner 在每轮转盘后调用 — shadow/live 都会登记）
# ----------------------------------------------------------------------

def enqueue_follow_ups(
    store, parent_event: WheelEvent, follow_ups: list[FollowUp], now: datetime,
) -> int:
    """把转盘输出的 FollowUp 落库为 wheel_events，含 §3.3 全部约束。"""
    enqueued = 0
    for fu in follow_ups:
        attempt = parent_event.attempt + 1
        if attempt > fu.max_attempts:
            continue

        due = now + fu.after
        if not _in_session(due.time()):
            if due.time() < _AFTERNOON[0]:
                due = _next_session_start(due)
            else:
                continue                        # 过收盘时间，不跨日

        until = fu.until or time(14, 30)
        if due.time() > until:
            continue

        position_id = (fu.subject or {}).get("position_id")
        subject_key = position_id or (fu.subject or {}).get("symbol") or "?"
        if position_id:
            try:
                pos = store.get_position(position_id)
            except Exception:  # noqa: BLE001
                pos = None
            if not pos or pos.get("status") != "open":
                continue                        # 持仓已平 → 不登记
            if store.inflight_followup_exists(
                fu.type, position_id, exclude_event_id=parent_event.event_id,
            ):
                continue                        # 同类后续事件最多 1 条在途

        expires_at = datetime.combine(due.date(), until)
        event_id = hashlib.sha1(
            f"{fu.type}|{subject_key}|{due:%Y-%m-%d %H:%M}".encode(),
        ).hexdigest()[:20]

        if store.insert_wheel_event({
            "event_id": event_id,
            "type": fu.type,
            "persona_id": (fu.payload or {}).get("persona_id") or parent_event.persona_id,
            "subject": dict(fu.subject or {}),
            "payload": dict(fu.payload or {}),
            "source": f"wheel:{(fu.payload or {}).get('wheel') or 'followup'}",
            "parent_event_id": parent_event.event_id,
            "attempt": attempt,
            "max_attempts": fu.max_attempts,
            "due_at": due.isoformat(),
            "expires_at": expires_at.isoformat(),
        }):
            enqueued += 1
            logger.info(
                "[Dispatcher] 登记后续事件 %s attempt=%d due=%s",
                fu.type, attempt, due.strftime("%H:%M"),
            )
    return enqueued


# ----------------------------------------------------------------------
# 单事件分发
# ----------------------------------------------------------------------

def _row_to_event(row: dict) -> WheelEvent:
    import json
    return WheelEvent(
        event_id=row["event_id"],
        type=row["type"],
        due_at=datetime.fromisoformat(row["due_at"]),
        persona_id=row.get("persona_id"),
        subject=json.loads(row.get("subject_json") or "{}"),
        payload=json.loads(row.get("payload_json") or "{}"),
        source=row.get("source") or "dispatcher",
        parent_event_id=row.get("parent_event_id"),
        attempt=int(row.get("attempt") or 1),
        max_attempts=int(row.get("max_attempts") or 1),
        expires_at=(
            datetime.fromisoformat(row["expires_at"]) if row.get("expires_at") else None
        ),
    )


def _position_open(store, subject: dict) -> bool:
    pid = subject.get("position_id")
    if not pid:
        return True
    try:
        pos = store.get_position(pid)
    except Exception:  # noqa: BLE001
        pos = None
    return bool(pos and pos.get("status") == "open" and int(pos.get("current_qty") or 0) > 0)


def _run_bound(cfg, binding, event, persona_pid, brain, now, subject=None) -> None:
    from src.wheel.persona import persona_policy
    from src.wheel.runner import run_binding

    ev = event if subject is None else WheelEvent(
        event_id=f"{event.event_id}:{subject.get('position_id')}",
        type=event.type, due_at=event.due_at, persona_id=persona_pid,
        subject=subject, payload=event.payload, source=event.source,
        parent_event_id=event.event_id,
        attempt=event.attempt, max_attempts=event.max_attempts,
        expires_at=event.expires_at,
    )
    persona = persona_policy(cfg, persona_pid, binding)
    run_binding(cfg, binding, ev, persona, brain, now=now)


def _dispatch_binding(cfg, row: dict, brain, now) -> None:
    """对一个 persona+binding 执行。urgent_review 输出逐持仓展开。

    单个角色/持仓失败不影响其余：逐个隔离异常，全部跑完后若有失败再整体
    上抛（事件记 failed，错误信息带失败对象）。
    """
    store = brain.store
    failures: list[str] = []

    def _safe(label: str, **kw) -> None:
        try:
            _run_bound(cfg, binding, _row_to_event(row), binding_persona, brain, now, **kw)
        except Exception as e:  # noqa: BLE001
            logger.error("[Dispatcher] %s 执行失败: %s", label, e, exc_info=True)
            failures.append(f"{label}: {e}"[:120])

    for binding_persona, binding in row["_bindings"]:
        wheel = cfg.wheels[binding.wheel] if binding.wheel else None
        if wheel is not None and not binding.chain and wheel.output == "urgent_review":
            # 市场事件 → 对每个在持持仓各跑一次（subject 展开）
            try:
                positions = store.list_positions(status="open", persona_id=binding_persona)
            except Exception as e:  # noqa: BLE001
                failures.append(f"{binding_persona}: list_positions {e}"[:120])
                continue
            for pos in positions:
                _safe(
                    f"{binding_persona}/{pos['position_id']}",
                    subject={"position_id": pos["position_id"]},
                )
        else:
            _safe(binding_persona)
    if failures:
        raise RuntimeError(f"{len(failures)} 个对象失败: " + "; ".join(failures[:3]))


def dispatch_once(brain, now: datetime | None = None) -> dict[str, int]:
    """处理一轮到期事件。返回各状态计数（测试与运维可见）。"""
    from src.wheel.config import get_config

    now = now or datetime.now()
    store = brain.store
    stats: dict[str, int] = {
        "done": 0, "failed": 0, "expired": 0, "cancelled": 0, "deferred": 0, "skipped": 0,
    }
    try:
        cfg = get_config()
    except Exception:  # noqa: BLE001
        logger.error("[Dispatcher] wheels.yaml 加载失败，本轮跳过", exc_info=True)
        return stats

    for row in store.due_wheel_events(now):
        event_type = row.get("type") or ""
        if not event_type.startswith(_HANDLED_PREFIXES):
            store.set_wheel_event_status(row["event_id"], "cancelled",
                                         error="非本阶段接管的事件类型")
            stats["skipped"] += 1
            continue

        eid = row["event_id"]
        if not is_trading_day(now.date()):
            store.set_wheel_event_status(eid, "expired", error="非交易日")
            stats["expired"] += 1
            continue
        if row.get("expires_at") and now > datetime.fromisoformat(row["expires_at"]):
            store.set_wheel_event_status(eid, "expired", error="超过截止时间")
            stats["expired"] += 1
            continue
        if int(row.get("attempt") or 1) > int(row.get("max_attempts") or 1):
            store.set_wheel_event_status(eid, "expired", error="超过复查次数上限")
            stats["expired"] += 1
            continue
        if not _in_session(now.time()):
            if now.time() < _AFTERNOON[0]:
                store.defer_wheel_event(eid, _next_session_start(now))
                stats["deferred"] += 1
            else:
                store.set_wheel_event_status(eid, "expired", error="已收盘")
                stats["expired"] += 1
            continue

        subject = _row_to_event(row).subject
        if not _position_open(store, subject):
            store.set_wheel_event_status(eid, "cancelled", error="持仓已平")
            stats["cancelled"] += 1
            continue

        # 绑定解析：指定 persona → 只查它；广播 → 所有绑定该事件的角色
        bindings: list[tuple[str, Any]] = []
        pid = row.get("persona_id")
        if pid:
            b = cfg.binding_for(pid, event_type)
            if b:
                bindings.append((pid, b))
        else:
            for candidate in cfg.personas.values():
                b = cfg.binding_for(candidate.persona_id, event_type)
                if b:
                    bindings.append((candidate.persona_id, b))
        if not bindings:
            store.set_wheel_event_status(eid, "done", error="无绑定，忽略")
            stats["done"] += 1
            continue

        store.set_wheel_event_status(eid, "running")
        row["_bindings"] = bindings
        fut = _executor.submit(_dispatch_binding, cfg, row, brain, now)
        try:
            fut.result(timeout=EXEC_TIMEOUT_S)
            store.set_wheel_event_status(eid, "done")
            stats["done"] += 1
        except TimeoutError:
            store.set_wheel_event_status(eid, "failed", error=f"执行超时 {EXEC_TIMEOUT_S}s")
            stats["failed"] += 1
            _notify("转盘事件执行超时", f"{eid} type={event_type}")
        except Exception as e:  # noqa: BLE001
            store.set_wheel_event_status(eid, "failed", error=str(e)[:200])
            stats["failed"] += 1
            logger.error("[Dispatcher] %s 执行失败: %s", eid, e, exc_info=True)
    return stats


def recover_running_events(brain) -> int:
    """进程重启后：running 超时的事件回退 due（转盘需幂等）。"""
    try:
        return brain.store.recover_running_wheel_events(RECOVER_AFTER_S)
    except Exception:  # noqa: BLE001
        logger.warning("[Dispatcher] running 事件回退失败", exc_info=True)
        return 0


def _notify(title: str, body: str) -> None:
    try:
        from src.infra.notify import notify_ops
        notify_ops(title, body)
    except Exception:  # noqa: BLE001
        logger.debug("notify_ops 失败", exc_info=True)


def dispatcher_loop(stop: threading.Event, brain=None, poll_seconds: int = POLL_SECONDS) -> None:
    """常驻循环；任何单轮异常都不退出，不拖垮 scheduler。"""
    from src.central_brain import get_central_brain
    brain = brain or get_central_brain()
    recovered = recover_running_events(brain)
    if recovered:
        logger.warning("[Dispatcher] 启动时回退 %d 个 running 事件", recovered)
    logger.info("[Dispatcher] 事件分发器启动（轮询 %ds）", poll_seconds)
    while not stop.is_set():
        try:
            stats = dispatch_once(brain)
            if any(v for k, v in stats.items() if k != "skipped"):
                logger.info("[Dispatcher] 本轮: %s", stats)
        except Exception:  # noqa: BLE001
            logger.error("[Dispatcher] 轮询异常", exc_info=True)
        stop.wait(poll_seconds)
