"""ExitGateway — 所有卖出类决策的统一出口（系统底线，不可配置）。

任何转盘给出的 EXIT/REDUCE 都必须经过这里：
1. 持仓锁：同一 position_id 同时只允许一个出场决策执行。
2. 同一事件窗口去重：同一分钟内对同一持仓的出场决策合并为最强的一个。
3. REDUCE 每持仓每交易日最多 1 次。
4. 真实价校验、T+1 前置校验（ExecutionEngine 仍做兜底）。
5. 执行后取消该持仓所有在途后续事件。

shadow 模式只做检查和记录，不调用 ExecutionEngine。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from src.wheel.protocol import Context, Decision, wheel_cohort

logger = logging.getLogger(__name__)

LOCK_TTL = timedelta(minutes=2)
DEDUP_WINDOW_S = 60
_LOCK_FACTOR = "exit_gateway.lock"


class ExitGateway:
    def __init__(self, brain) -> None:
        self.brain = brain
        self.store = brain.store

    def submit(
        self, decision: Decision, ctx: Context, *, decision_id: str = "",
    ) -> dict:
        """提交一个出场决策。返回 {"accepted", "executed", "reason"}。

        decision_id 对应 wheel_decisions 主键：通过网关（shadow=会执行、
        live=已执行）时打 executed 标记，同窗口去重依赖该标记。
        """
        if decision.action not in ("EXIT", "REDUCE"):
            return {"accepted": False, "executed": False, "reason": "非卖出动作"}

        position_id = (ctx.event.subject or {}).get("position_id") or decision.subject_key
        symbol = ""
        persona_id = ctx.persona.persona_id

        # 1. 持仓必须仍然存在且数量>0
        try:
            fresh = self.store.get_position(position_id)
        except Exception:  # noqa: BLE001
            fresh = None
        if not fresh or fresh.get("status") != "open" or int(fresh.get("current_qty") or 0) <= 0:
            return self._skip(decision, "position_not_open")
        symbol = fresh.get("symbol", "")
        persona_id = fresh.get("persona_id") or persona_id

        # 2. 持仓锁：已有出场在执行中
        if self._locked(position_id, ctx.now):
            return self._skip(decision, "exit_in_progress")

        # 3. 同一窗口去重：已有相同或更强动作在执行/已执行
        if self._duplicate_in_window(position_id, decision, ctx.now, mode=ctx.mode):
            return self._skip(decision, "duplicate_in_window")

        # 4. 真实价校验
        if float(decision.price or 0) <= 0:
            return self._skip(decision, "no_real_price")

        # 5. T+1 前置校验
        entry_date = fresh.get("entry_date") or ""
        if entry_date == ctx.now.date().isoformat():
            return self._skip(decision, "t1_locked")

        # 6. REDUCE 每日限频（系统底线，不依赖 modify 配置）
        if decision.action == "REDUCE" and self.store.sold_today(
            persona_id, symbol, today=ctx.now.date(),
        ):
            return self._skip(decision, "reduce_daily_cap")

        if ctx.mode != "live":
            logger.info(
                "[ExitGateway:shadow] %s %s %s — 不执行 (%s)",
                persona_id, decision.action, symbol, decision.reason,
            )
            # shadow 模式通过网关即视为"会执行" — executed 标记让同窗口
            # 去重在影子模式下也生效，影子语义与 live 一致
            self._mark_executed(decision_id)
            return {"accepted": True, "executed": False, "reason": "shadow"}

        # 7. live：加锁 → 执行 → 取消后续事件
        self._acquire(position_id, ctx.now)
        try:
            result = self._execute(ctx, fresh, decision)
        except Exception as e:  # noqa: BLE001
            # 执行中异常：可能已成交（如成交后 close_position 失败）。不上抛 —
            # 上抛会让调用方拿到 None 走旧路径兜底，造成重复卖出。
            logger.error(
                "[ExitGateway] %s %s 执行异常（可能已成交）: %s",
                symbol, decision.action, e, exc_info=True,
            )
            self._notify_ambiguous(persona_id, symbol, decision, f"execute_error: {e}")
            # 锁保留至 TTL 到期，挡住下一轮扫描对同一持仓的重复提交
            return {"accepted": True, "executed": False, "reason": "execute_error"}
        if result.get("reason") == "execute_timeout":
            # 超时时执行线程可能仍在跑 — 同样保留锁
            self._notify_ambiguous(persona_id, symbol, decision, "execute_timeout")
        else:
            self._release(position_id)
        if result.get("executed"):
            self._mark_executed(decision_id)
            self._cancel_follow_ups(position_id)
        return {"accepted": True, "executed": bool(result.get("executed")),
                "reason": result.get("reason", ""), "result": result}

    # ------------------------------------------------------------------

    def _skip(self, decision: Decision, reason: str) -> dict:
        logger.info(
            "[ExitGateway] %s %s 跳过: %s",
            decision.subject_key, decision.action, reason,
        )
        return {"accepted": False, "executed": False, "reason": reason}

    @staticmethod
    def _notify_ambiguous(persona_id: str, symbol: str, decision: Decision, reason: str) -> None:
        try:
            from src.infra.notify import notify_ops
            notify_ops(
                "出场网关执行结果不确定",
                f"persona={persona_id} symbol={symbol} action={decision.action} "
                f"reason={reason} — 请人工核对成交与持仓",
            )
        except Exception:  # noqa: BLE001
            logger.debug("notify_ops 失败", exc_info=True)

    def _locked(self, position_id: str, now: datetime) -> bool:
        try:
            state = self.store.get_factor_state("*", _LOCK_FACTOR, position_id)
        except Exception:  # noqa: BLE001
            return False
        if not state:
            return False
        try:
            at = datetime.fromisoformat(state.get("locked_at", ""))
        except ValueError:
            return False
        return now - at < LOCK_TTL

    def _acquire(self, position_id: str, now: datetime) -> None:
        try:
            self.store.set_factor_state(
                "*", _LOCK_FACTOR, position_id, {"locked_at": now.isoformat()},
            )
        except Exception:  # noqa: BLE001
            logger.warning("exit lock 获取失败 %s", position_id, exc_info=True)

    def _release(self, position_id: str) -> None:
        try:
            self.store.clear_factor_state("*", _LOCK_FACTOR, position_id)
        except Exception:  # noqa: BLE001
            pass

    def _mark_executed(self, decision_id: str) -> None:
        if not decision_id:
            return
        try:
            self.store.mark_wheel_decision_executed(decision_id)
        except Exception:  # noqa: BLE001
            logger.warning("wheel_decision executed 标记失败 %s", decision_id, exc_info=True)

    def _duplicate_in_window(
        self, position_id: str, decision: Decision, now: datetime, *, mode: str = "live",
    ) -> bool:
        """同一分钟内已有相同/更强出场决策被执行 → 本次跳过（EXIT 最强）。

        live：只看 live 决策、不分转盘（同一持仓只有一个出场方；影子的
        "会执行"不能挡掉真实卖出）。shadow：只看同组转盘的影子决策 — 新转盘
        的影子"会执行"不能挡掉门槛转盘，否则会把"转盘更松"掩盖成"网关拒绝"。
        """
        try:
            recent = self.store.recent_wheel_decision(
                subject_key=position_id,
                actions=("EXIT", "REDUCE"),
                within_seconds=DEDUP_WINDOW_S,
                executed_only=True,
                now=now,
                wheels=None if mode == "live" else wheel_cohort(decision.wheel),
                mode="live" if mode == "live" else "shadow",
            )
        except Exception:  # noqa: BLE001
            return False
        if not recent:
            return False
        return recent["action"] == "EXIT" or decision.action != "EXIT"

    def _cancel_follow_ups(self, position_id: str) -> None:
        try:
            n = self.store.cancel_wheel_events_for_position(position_id)
            if n:
                logger.info("[ExitGateway] 取消 %s 的 %d 个在途后续事件", position_id, n)
        except Exception:  # noqa: BLE001
            logger.debug("取消后续事件失败 %s", position_id, exc_info=True)

    def _execute(self, ctx: Context, position: dict, decision: Decision) -> dict:
        """live 执行：复用统一卖出通道（Phase 6 再内聚到网关内）。

        _execute_review_action 是 async；若当前线程已有事件循环（shadow/live
        嵌在 asyncio.run 里），则在独立线程中执行并等待结果。
        """
        from src.agents.reviewer.intraday_loop import _execute_review_action
        import asyncio
        import threading

        review = {
            "action": decision.action,
            "current_price": decision.price,
            "reason": f"[wheel:{decision.wheel}] {decision.reason}",
            "risk_flag": decision.risk_flag,
        }
        coro = _execute_review_action(
            self.brain, ctx.event.event_id, position, review,
            persona_id=ctx.persona.persona_id,
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        box: dict = {}

        def _run() -> None:
            try:
                box["result"] = asyncio.run(coro)
            except Exception as e:  # noqa: BLE001
                box["error"] = e

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=60)
        if "error" in box:
            raise box["error"]
        return box.get("result") or {"executed": False, "reason": "execute_timeout"}
