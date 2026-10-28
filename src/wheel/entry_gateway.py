"""EntryGateway — 所有买入类决策的统一出口（Phase 3，与 ExitGateway 对称）。

职责（docs/decision_wheel_architecture.md §6.3）：
1. 汇总同一 (event_id, persona_id) 下的 BUY 提议，按 (-score, priority, symbol) 跨标的排序；
2. 截断 Top-N（max_entries，如隔夜客尾盘狙击取第 1 名）并校验最大持仓只数（max_open_positions）；
3. 强制执行系统底线与组合级风控硬闸门：
   - data_quality 硬闸（mock / degraded / unavailable 严禁开新仓）
   - 真实价格校验（price > 0）
   - 角色策略白名单（如隔夜客仅允许 ultra_short_momentum）
   - 同标的重复持仓拦截
   - 账户可用资金下限（min_cash）
   - 组合总仓位上限（max_total_position_pct）与单板块集中度上限（max_sector_concentration_pct）
   - live 模式下按 (persona_id, symbol) 原子加锁，防止并发重复建仓。

shadow 模式只做排序、风控校验与记录（accepted / rejected:<reason>），不调用 ExecutionEngine。
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import logging
import uuid

from src.infra.data_quality import blocks_new_entries
from src.wheel.protocol import Context, Decision

logger = logging.getLogger(__name__)

LOCK_TTL = timedelta(minutes=2)
_LOCK_FACTOR = "entry_gateway.lock"
_EXEC_TIMEOUT_S = 15
_exec_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="entry-gw")


class EntryGateway:
    def __init__(self, brain) -> None:
        self.brain = brain
        self.store = brain.store

    def submit(
        self, decision: Decision, ctx: Context, *, decision_id: str = "",
    ) -> dict:
        """提交单条入场决策。返回 {"accepted", "executed", "reason"}。"""
        did = decision_id or decision.decision_id
        outcomes = self.submit_batch([decision], ctx)
        return outcomes.get(did or decision.subject_key, {
            "accepted": False, "executed": False, "reason": "非买入动作",
        })

    def submit_batch(
        self, decisions: list[Decision], ctx: Context,
    ) -> dict[str, dict]:
        """批量裁决同一事件下的多个候选决策（跨标的 Top-N 排序 + 组合风控）。"""
        outcomes: dict[str, dict] = {}
        buy_decisions = [d for d in decisions if d.action in ("BUY", "ADD")]
        for d in decisions:
            if d.action not in ("BUY", "ADD"):
                key = d.decision_id or d.subject_key
                outcomes[key] = {"accepted": False, "executed": False, "reason": "非买入动作"}

        if not buy_decisions:
            return outcomes

        persona_id = ctx.persona.persona_id or "short_term_hot_rotation_v1"
        is_ultra = ctx.persona.kind == "ultra" or persona_id == "ultra_short_momentum_v1"
        is_live = ctx.mode == "live"

        # 排序：score 降序 → priority 升序 → subject_key 升序
        sorted_buys = sorted(
            buy_decisions,
            key=lambda d: (
                -float(d.evidence.get("score") or 0.0),
                int(d.evidence.get("priority") or 100),
                d.subject_key,
            ),
        )

        # 读取账户、当前持仓与最新市场状态
        account = None
        open_positions: list[dict] = []
        market_regime: dict = dict(ctx.data.get("regime_snapshot") or {})
        try:
            account = self.store.get_account_by_persona(persona_id)
        except Exception:  # noqa: BLE001
            pass
        try:
            open_positions = self.store.list_open_positions(persona_id=persona_id)
        except Exception:  # noqa: BLE001
            pass
        if not market_regime:
            try:
                market_regime = self.store.latest_market_regime() or {}
            except Exception:  # noqa: BLE001
                market_regime = {}

        capital = float(account.get("total_value") or account.get("capital") or 300000.0) if account else 300000.0
        available_cash = float(account.get("available_cash", 300000.0)) if account else 300000.0
        open_symbols = {str(p.get("symbol") or "") for p in open_positions}
        used_cost = sum(
            float(p.get("entry_price") or 0) * int(p.get("current_qty") or 0)
            for p in open_positions
        )
        used_pct = used_cost / capital if capital > 0 else 0.0
        sector_pct: dict[str, float] = {}
        for p in open_positions:
            sec = str(p.get("sector") or "")
            if sec and capital > 0:
                cost = float(p.get("entry_price") or 0) * int(p.get("current_qty") or 0)
                sector_pct[sec] = sector_pct.get(sec, 0.0) + cost / capital

        max_entries = int(ctx.persona.params.get("max_entries", 1 if is_ultra else 5))
        max_open = int(ctx.persona.params.get("max_open_positions", 2 if is_ultra else 10))
        min_cash = float(ctx.persona.params.get("min_cash", 10000.0))
        max_total_pct = float(
            ctx.persona.params.get(
                "max_total_position_pct",
                market_regime.get("max_total_position_pct") or (0.40 if is_ultra else 0.80),
            )
        )
        max_sector_pct = float(ctx.persona.params.get("max_sector_concentration_pct", 0.40))

        accepted_count = 0
        for d in sorted_buys:
            key = d.decision_id or d.subject_key
            symbol = str(d.evidence.get("symbol") or (ctx.event.subject or {}).get("symbol") or d.subject_key)
            price = float(d.price or d.evidence.get("price") or 0.0)
            qty_pct = float(d.qty_pct or d.evidence.get("position_pct") or (0.20 if is_ultra else 0.05))
            strategy_id = str(d.evidence.get("strategy_id") or d.evidence.get("strategy") or "")
            sector = str(d.evidence.get("sector") or "")

            def _reject(reason: str) -> dict:
                logger.info("[EntryGateway] %s %s 拒绝: %s", persona_id, symbol, reason)
                return {"accepted": False, "executed": False, "reason": reason}

            # 1. 系统底线：data_quality 硬闸
            if blocks_new_entries(market_regime):
                outcomes[key] = _reject("data_quality_blocked")
                continue

            # 2. 真实价格校验
            if price <= 0:
                outcomes[key] = _reject("no_real_price")
                continue

            # 3. 角色策略白名单
            if is_ultra and strategy_id and strategy_id != "ultra_short_momentum":
                outcomes[key] = _reject("strategy_mismatch")
                continue

            # 4. 重复持仓检查
            if d.action == "BUY" and symbol in open_symbols:
                outcomes[key] = _reject("duplicate_position")
                continue

            # 5. 在持仓只数上限
            if d.action == "BUY" and (len(open_positions) + accepted_count) >= max_open:
                outcomes[key] = _reject("max_open_positions")
                continue

            # 6. Top-N 截断
            if accepted_count >= max_entries:
                outcomes[key] = _reject("ranked_out_of_top_n")
                continue

            # 7. 可用资金下限
            if available_cash < min_cash:
                outcomes[key] = _reject("insufficient_cash")
                continue

            # 8. 组合总仓位上限
            if used_pct + qty_pct > max_total_pct + 1e-6:
                remaining = max_total_pct - used_pct
                if remaining < 0.02:
                    outcomes[key] = _reject("total_position_exceeded")
                    continue
                qty_pct = round(remaining, 4)

            # 9. 单板块集中度上限
            if sector and sector not in ("未知", ""):
                cur_sec = sector_pct.get(sector, 0.0)
                if cur_sec + qty_pct > max_sector_pct + 1e-6:
                    outcomes[key] = _reject("sector_concentration")
                    continue

            lock_key = f"{persona_id}:{symbol}"
            if is_live:
                if not self._acquire(lock_key, ctx.now):
                    outcomes[key] = _reject("entry_in_progress")
                    continue

            if not is_live:
                logger.info(
                    "[EntryGateway:shadow] %s BUY %s @ %.2f — 不执行 (%s)",
                    persona_id, symbol, price, d.reason,
                )
                self._mark_executed(d.decision_id)
                accepted_count += 1
                open_symbols.add(symbol)
                used_pct += qty_pct
                if sector:
                    sector_pct[sector] = sector_pct.get(sector, 0.0) + qty_pct
                outcomes[key] = {"accepted": True, "executed": False, "reason": "shadow"}
                continue

            # 10. live 执行
            try:
                exec_res = self._execute(ctx, d, persona_id, symbol, price, qty_pct, capital, available_cash)
            except Exception as e:  # noqa: BLE001
                logger.error("[EntryGateway] %s BUY %s 执行异常: %s", persona_id, symbol, e, exc_info=True)
                outcomes[key] = {"accepted": True, "executed": False, "reason": "execute_error"}
                continue
            finally:
                self._release(lock_key)

            if exec_res.get("executed"):
                self._mark_executed(d.decision_id)
                accepted_count += 1
                open_symbols.add(symbol)
                used_pct += qty_pct
                available_cash = max(0.0, available_cash - price * int(exec_res.get("quantity") or 0))
                if sector:
                    sector_pct[sector] = sector_pct.get(sector, 0.0) + qty_pct
                outcomes[key] = {
                    "accepted": True,
                    "executed": True,
                    "reason": "executed",
                    "result": exec_res,
                }
            else:
                outcomes[key] = {
                    "accepted": False,
                    "executed": False,
                    "reason": str(exec_res.get("reason") or "execution_rejected"),
                    "result": exec_res,
                }

        return outcomes

    def _acquire(self, lock_key: str, now: datetime) -> bool:
        try:
            return self.store.try_acquire_factor_lock(
                "*", _LOCK_FACTOR, lock_key, now, LOCK_TTL,
            )
        except Exception:  # noqa: BLE001
            logger.error("entry lock 获取失败 %s", lock_key, exc_info=True)
            return False

    def _release(self, lock_key: str) -> None:
        try:
            self.store.clear_factor_state("*", _LOCK_FACTOR, lock_key)
        except Exception:  # noqa: BLE001
            logger.warning("entry lock 释放失败 %s", lock_key, exc_info=True)

    def _mark_executed(self, decision_id: str) -> None:
        if not decision_id:
            return
        try:
            self.store.mark_wheel_decision_executed(decision_id)
        except Exception:  # noqa: BLE001
            logger.debug("wheel_decision executed 标记失败 %s", decision_id, exc_info=True)

    def _execute(
        self,
        ctx: Context,
        decision: Decision,
        persona_id: str,
        symbol: str,
        price: float,
        qty_pct: float,
        capital: float,
        available_cash: float,
    ) -> dict:
        from src.agents.executioner.executor import ExecutionEngine

        ev = decision.evidence or {}
        target_amount = min(capital * qty_pct, available_cash * 0.90)
        qty = int(ev.get("target_qty") or max(int(target_amount / price // 100) * 100, 100))
        strategy_id = str(ev.get("strategy_id") or ("ultra_short_momentum" if ctx.persona.kind == "ultra" else "value_investing"))
        stop_loss = float(ev.get("stop_loss") or round(price * (0.975 if ctx.persona.kind == "ultra" else 0.92), 2))
        target_price = float(ev.get("target_price") or round(price * 1.08, 2))
        sig_id = str(ev.get("signal_id") or f"SIG-WHL-{uuid.uuid4().hex[:8].upper()}")

        signal = {
            "signal_id": sig_id,
            "symbol": symbol,
            "name": str(ev.get("name") or symbol),
            "sector": str(ev.get("sector") or ""),
            "action": "buy",
            "entry_price": price,
            "current_price": price,
            "market_price_at_trigger": price,
            "target_price": target_price,
            "stop_loss": stop_loss,
            "position_pct": round(qty_pct, 4),
            "target_qty": qty,
            "strategy": str(ev.get("strategy") or strategy_id),
            "strategy_id": strategy_id,
            "entry_condition": "immediate",
            "rationale": decision.reason,
            "timestamp": ctx.now.isoformat(),
            "expiry": None,
        }

        session_id = f"wheel-entry-{ctx.event.event_id}"

        def _coro():
            engine = ExecutionEngine(session_id, persona_id=persona_id, brain=self.brain)
            return asyncio.run(engine.monitor_and_execute([signal]))

        fut = _exec_pool.submit(_coro)
        orders, fills = fut.result(timeout=_EXEC_TIMEOUT_S)
        if fills:
            watch_id = ev.get("watch_id")
            if watch_id:
                try:
                    self.store.trigger_watchlist_item(str(watch_id))
                except Exception:  # noqa: BLE001
                    pass
            if ev.get("signal_id"):
                try:
                    self.store.update_signal_status(str(ev["signal_id"]), "filled")
                except Exception:  # noqa: BLE001
                    pass
            return {
                "executed": True,
                "symbol": symbol,
                "name": signal["name"],
                "fill_price": fills[0]["avg_price"],
                "quantity": fills[0]["quantity"],
                "setup": ev.get("setup", ""),
                "orders": orders,
                "fills": fills,
            }
        reason = "execution_rejected"
        if orders and orders[0].get("status") == "rejected":
            reason = "order_rejected"
        return {"executed": False, "symbol": symbol, "reason": reason, "orders": orders, "fills": []}
