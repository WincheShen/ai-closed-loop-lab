"""Execution Engine — 执行者核心实现。

职责：
1. 接收 TradeSignal，按交易模式执行下单
2. 成交后自动创建 Position 记录（fills → positions 桥接）
3. 严格隔离：mock / shadow / live

⚠️ 安全设计：
- TRADING_MODE=mock 时只记录到数据库，不对接券商
- TRADING_MODE=shadow 时双轨执行：mock + 实盘同时运行，对比结果
- TRADING_MODE=live 时只走真实接口，且由安全护栏严格限制
- Circuit Breaker: 日亏损超限或连续亏损过多时自动停盘
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from src.central_brain import get_central_brain
from src.graph.state import Fill, Order, TradeSignal, TradingState
from src.infra.config import cfg
from src.infra.logger import get_agent_logger

logger = get_agent_logger("executioner", "init")

# 安全常量
MIN_SHARE_UNIT = 100
DAILY_LOSS_LIMIT_PCT = -0.03       # 日亏损 3% 熔断
MAX_CONSECUTIVE_LOSSES = 5         # 连续亏损 5 笔熔断


class ExecutionEngine:
    """执行者引擎。"""

    def __init__(self, session_id: str, persona_id: str | None = None) -> None:
        self.session_id = session_id
        self.persona_id = persona_id or "short_term_hot_rotation_v1"
        self.logger = get_agent_logger("executioner", session_id)
        self.brain = get_central_brain()
        self.mode = cfg().get("trading_mode", "mock")
        self.submitted_orders: list[Order] = []
        self.filled_orders: list[Fill] = []

    # ------------------------------------------------------------------
    # Circuit Breaker — 熔断检查
    # ------------------------------------------------------------------

    def _check_circuit_breaker(self) -> bool:
        """检查是否应触发熔断。返回 True 表示应停止交易。"""
        today = datetime.now().strftime("%Y-%m-%d")

        # 1) 日内已实现亏损检查
        closed_positions = self.brain.store.list_positions(
            persona_id=self.persona_id, status="closed",
        )
        closed_today = [
            p for p in closed_positions
            if (p.get("closed_at") or "").startswith(today)
        ]
        daily_pnl = sum(p.get("realized_pnl", 0) or 0 for p in closed_today)

        account = self.brain.store.get_account_by_persona(self.persona_id)
        capital = cfg().get("initial_capital", 300000)
        if account:
            capital = account.get("available_cash", 0) + sum(
                p.get("entry_price", 0) * p.get("current_qty", 0)
                for p in self.brain.store.list_open_positions(persona_id=self.persona_id)
            )

        daily_limit = capital * DAILY_LOSS_LIMIT_PCT
        if daily_pnl < daily_limit:
            self.logger.warning(
                "⚠️ CIRCUIT BREAKER: 日内亏损 %.2f 超过限制 %.2f，停止买入",
                daily_pnl, daily_limit,
            )
            return True

        # 2) 连续亏损检查
        recent = sorted(closed_positions, key=lambda p: p.get("closed_at", ""), reverse=True)
        consecutive_losses = 0
        for p in recent[:MAX_CONSECUTIVE_LOSSES + 2]:
            if (p.get("realized_pnl") or 0) < 0:
                consecutive_losses += 1
            else:
                break

        if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            self.logger.warning(
                "⚠️ CIRCUIT BREAKER: 连续亏损 %d 笔(限制 %d)，停止买入",
                consecutive_losses, MAX_CONSECUTIVE_LOSSES,
            )
            return True

        return False

    # ------------------------------------------------------------------
    # SUSPEND 硬门槛 — 策略暂停过滤
    # ------------------------------------------------------------------

    def _filter_suspended_signals(self, signals: list[TradeSignal]) -> list[TradeSignal]:
        """过滤被经验层标记为 SUSPEND 的策略信号（仅阻止买入，卖出放行）。"""
        try:
            from src.experience_layer import get_experience
            exp = get_experience()
        except Exception:
            self.logger.debug("无法加载 ExperienceLayer，跳过 SUSPEND 检查")
            return signals

        latest_regime = self.brain.store.latest_market_regime()
        regime = latest_regime.get("regime", "") if latest_regime else ""

        filtered = []
        rejected = 0
        for sig in signals:
            # 卖出信号始终放行
            if sig.get("action") != "buy":
                filtered.append(sig)
                continue

            strategy_id = sig.get("strategy_id", "")
            if not strategy_id or strategy_id == "unknown":
                filtered.append(sig)
                continue

            try:
                stats = exp.get_strategy_stats(strategy_id, regime=regime or None)
                if stats and stats.get("recommendation") == "SUSPEND":
                    self.logger.warning(
                        "SUSPEND REJECT %s %s | 策略 %s 在 %s 下已暂停 "
                        "(胜率 %.0f%%, 样本 %d)",
                        sig.get("signal_id", ""), sig["symbol"],
                        strategy_id, regime or "all",
                        stats["win_rate"] * 100, stats["total_trades"],
                    )
                    self.brain.store.update_signal_status(
                        sig["signal_id"], "rejected_strategy_suspended",
                    )
                    rejected += 1
                    continue
            except Exception as e:
                # SUSPEND 检查失败时，安全侧倒：拒绝信号
                self.logger.error(
                    "SUSPEND 检查异常 %s: %s，安全起见拒绝",
                    sig["symbol"], e,
                )
                self.brain.store.update_signal_status(
                    sig["signal_id"], "rejected_suspend_check_error",
                )
                rejected += 1
                continue

            filtered.append(sig)

        if rejected:
            self.logger.info("SUSPEND 过滤: %d/%d 信号被拒绝", rejected, len(signals))

        return filtered

    # ------------------------------------------------------------------
    # 主执行循环
    # ------------------------------------------------------------------

    async def monitor_and_execute(
        self, signals: list[TradeSignal],
    ) -> tuple[list[Order], list[Fill]]:
        """主循环：监控信号并执行。

        分流逻辑:
        - entry_condition=immediate → 立即模拟成交
        - entry_condition=breakout/pullback → 存入观察池(pending), 由盘中循环跟踪
        """
        self.logger.info("开始盯盘 — 模式=%s, 信号数=%d", self.mode, len(signals))

        # SUSPEND 硬门槛 — 过滤掉被暂停策略的买入信号
        signals = self._filter_suspended_signals(signals)

        # 熔断检查 — 仅阻止买入，卖出始终允许
        breaker_tripped = self._check_circuit_breaker()
        buy_signals = [s for s in signals if s.get("action") == "buy"]
        sell_signals = [s for s in signals if s.get("action") != "buy"]

        if breaker_tripped and buy_signals:
            self.logger.warning(
                "熔断已触发，跳过 %d 个买入信号，保留 %d 个卖出信号",
                len(buy_signals), len(sell_signals),
            )
            signals = sell_signals

        pending_count = 0
        for sig in signals:
            condition = sig.get("entry_condition", "immediate")

            if condition in ("breakout", "pullback"):
                # mock 模式下条件单也立即执行（因为没有盘中循环监控）
                if self.mode == "mock":
                    self.logger.info(
                        "[MOCK] %s %s | 条件=%s → mock模式立即执行",
                        sig["symbol"], sig.get("name", ""), condition
                    )
                    # 继续执行，不标记为 pending
                else:
                    # real/paper 模式下条件单 → 不立即执行，标记为 pending 等待盘中触发
                    self.brain.store.update_signal_status(sig["signal_id"], "pending")
                    # 同步加入自选股池，附带入场条件
                    self._add_to_watchlist_from_signal(sig, condition)
                    pending_count += 1
                    condition_desc = (
                        f"突破{sig['entry_price']:.2f}" if condition == "breakout"
                        else f"回调{sig['entry_price']:.2f}"
                    )
                    self.logger.info(
                        "[PENDING] %s %s | 条件=%s | 当前=%.2f → 等待 %s → 已加入自选股",
                        sig["symbol"], sig.get("name", ""),
                        condition, sig.get("current_price", 0),
                        condition_desc,
                    )
                    continue

            # 立即执行（包括 mock 模式下的条件单）
            if self.mode == "mock":
                order, fill = await self._mock_execute(sig)
            elif self.mode == "shadow":
                order, fill = await self._shadow_execute(sig)
            elif self.mode == "live":
                order, fill = await self._live_execute(sig)
            else:
                # 未知模式，安全降级到 mock
                self.logger.warning("未知交易模式 '%s'，降级为 mock", self.mode)
                order, fill = await self._mock_execute(sig)

            self.submitted_orders.append(order)
            if fill:
                self.filled_orders.append(fill)
                self.brain.store.update_signal_status(sig["signal_id"], "filled")

        self.logger.info(
            "执行完成 — 立即成交 %d/%d 笔, 条件单(pending) %d 笔",
            len(self.filled_orders), len(self.submitted_orders), pending_count,
        )
        return self.submitted_orders, self.filled_orders

    async def _mock_execute(self, signal: TradeSignal) -> tuple[Order, Fill | None]:
        """模拟执行：按市场价成交，并自动建仓。

        P0 安全保证:
        - P0.1: 买入前先检查是否已有同标的持仓，避免重复建仓
        - P0.2: order + fill + position + balance 在同一事务内完成
        - P0.3: 价格护栏 — 必须有参考价，偏离过大则拒绝或修正
        """
        action = signal.get("action", "")
        symbol = signal["symbol"]

        # --- P0.1: 买入前重复持仓检查（在下单前拦截） ---
        if action == "buy":
            existing = self.brain.store.list_open_positions(persona_id=self.persona_id)
            if any(p["symbol"] == symbol for p in existing):
                self.logger.warning(
                    "[MOCK] ❌ %s 已有持仓，跳过买入信号 %s",
                    symbol, signal["signal_id"],
                )
                self.brain.store.update_signal_status(signal["signal_id"], "rejected_duplicate")
                rejected_order: Order = {
                    "order_id": f"ORD-{uuid.uuid4().hex[:8].upper()}",
                    "signal_id": signal["signal_id"],
                    "symbol": symbol,
                    "side": "buy",
                    "quantity": 0,
                    "order_type": "limit",
                    "limit_price": signal["entry_price"],
                    "status": "rejected",
                    "submitted_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }
                return rejected_order, None

        order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now().isoformat()

        # 动态计算下单数量（基于资金和仓位比例，100 股整数倍）
        # 对 sell 信号优先使用 target_qty（来自持仓实际数量）
        entry_price = signal["entry_price"]
        target_qty = signal.get("target_qty")

        # --- P0.3: 强化价格护栏 ---
        # 优先使用 market_price_at_trigger（由 _check_pending_signals 传入的实时价）
        # 其次用 signal 中的 current_price（信号生成时记录的价格）
        ref_price = signal.get("market_price_at_trigger") or signal.get("current_price") or 0

        if action == "buy" and entry_price > 0:
            if ref_price > 0:
                limit_deviation = abs(entry_price - ref_price) / ref_price
                if limit_deviation > 0.30:
                    self.logger.error(
                        "[MOCK] ❌ %s 限价 %.2f 偏离参考价 %.2f 达 %.0f%%，疑似数据异常，拒绝下单",
                        symbol, entry_price, ref_price, limit_deviation * 100,
                    )
                    self.brain.store.update_signal_status(signal["signal_id"], "rejected_price_anomaly")
                    rejected_order: Order = {
                        "order_id": order_id,
                        "signal_id": signal["signal_id"],
                        "symbol": symbol,
                        "side": "buy",
                        "quantity": 0,
                        "order_type": "limit",
                        "limit_price": entry_price,
                        "status": "rejected",
                        "submitted_at": now,
                        "updated_at": now,
                    }
                    return rejected_order, None
                elif limit_deviation > 0.10:
                    # 偏离 10-30%: 修正为参考价，并记录警告
                    self.logger.warning(
                        "[MOCK] ⚠️ %s entry_price=%.2f 偏离 ref_price=%.2f 达 %.0f%%，修正为参考价",
                        symbol, entry_price, ref_price, limit_deviation * 100,
                    )
                    entry_price = ref_price
            else:
                # 无参考价时拒绝买入（防止脏数据通过）
                self.logger.error(
                    "[MOCK] ❌ %s 无参考价可校验 entry_price=%.2f，拒绝下单",
                    symbol, entry_price,
                )
                self.brain.store.update_signal_status(signal["signal_id"], "rejected_no_ref_price")
                rejected_order: Order = {
                    "order_id": order_id,
                    "signal_id": signal["signal_id"],
                    "symbol": symbol,
                    "side": "buy",
                    "quantity": 0,
                    "order_type": "limit",
                    "limit_price": entry_price,
                    "status": "rejected",
                    "submitted_at": now,
                    "updated_at": now,
                }
                return rejected_order, None

        # Mock 模式用参考价（实时市场价）作为成交价，更真实模拟滑点
        fill_price = ref_price if (ref_price > 0 and action == "buy") else entry_price

        self.logger.info(
            "[_mock_execute] %s %s | target_qty=%s | action=%s",
            symbol, action, target_qty, action,
        )

        if target_qty and action == "sell":
            quantity = target_qty
            self.logger.info("[_mock_execute] 使用 target_qty: %d", quantity)
        else:
            account = self.brain.store.get_account_by_persona(self.persona_id)
            if account:
                available_cash = account.get("available_cash", 0)
                self.logger.info("[_mock_execute] 账户资金: persona=%s, available_cash=%.0f", self.persona_id, available_cash)
            else:
                available_cash = cfg().get("initial_capital", 300000)
                self.logger.warning("[_mock_execute] 账户未找到，使用默认资金: %.0f", available_cash)

            position_pct = signal.get("position_pct", 0.08)
            allocation = available_cash * position_pct
            quantity = max(100, int(allocation / fill_price / 100) * 100) if fill_price > 0 else 100
            self.logger.info(
                "[_mock_execute] 动态计算数量: cash=%.0f, pct=%.2f, allocation=%.0f, price=%.2f, qty=%d",
                available_cash, position_pct, allocation, fill_price, quantity,
            )

        order: Order = {
            "order_id": order_id,
            "signal_id": signal["signal_id"],
            "symbol": symbol,
            "side": "buy" if action == "buy" else "sell",
            "quantity": quantity,
            "order_type": "limit",
            "limit_price": fill_price,
            "status": "submitted",
            "submitted_at": now,
            "updated_at": now,
        }

        self.logger.info(
            "[MOCK] 模拟下单 %s | %s %s | 限价 %.2f | 数量 %d",
            order_id, symbol, order["side"], fill_price, quantity,
        )

        # --- P0.2: 事务原子性 — 全部成功或全部回滚 ---
        fill: Fill | None = None
        try:
            self.brain.store.begin_transaction()

            self.brain.store.save_order(order)

            # 模拟立即成交
            fill = {
                "fill_id": f"FIL-{uuid.uuid4().hex[:8].upper()}",
                "persona_id": self.persona_id,
                "order_id": order_id,
                "symbol": symbol,
                "side": order["side"],
                "quantity": quantity,
                "avg_price": fill_price,
                "fees": round(quantity * fill_price * 0.0003, 2),
                "filled_at": datetime.now().isoformat(),
            }
            self.brain.store.save_fill(fill)

            order["status"] = "filled"
            order["updated_at"] = fill["filled_at"]
            self.brain.store.save_order(order)

            # 更新账户资金
            self._update_account_after_trade(fill)

            # BUY 成交 → 自动创建 Position 记录
            if action == "buy":
                self._auto_open_position(signal, fill)

            self.brain.store.commit_transaction()
        except Exception:
            self.brain.store.rollback_transaction()
            self.logger.exception(
                "[MOCK] ❌ 事务失败，已回滚: %s %s %s", order_id, symbol, action,
            )
            order["status"] = "error"
            fill = None
            raise

        self.brain.bus.emit_order_fill(fill)
        return order, fill

    def _update_account_after_trade(self, fill: Fill) -> None:
        """成交后更新账户可用资金。"""
        account = self.brain.store.get_account_by_persona(self.persona_id)
        if not account:
            self.logger.warning("[_update_account] 账户未找到: persona=%s", self.persona_id)
            return

        trade_value = fill["quantity"] * fill["avg_price"]
        fees = fill.get("fees", 0)

        if fill["side"] == "buy":
            # 买入：减少可用资金
            delta = -(trade_value + fees)
            new_cash = account["available_cash"] + delta
            self.logger.info(
                "[_update_account] 买入扣款: persona=%s, 交易金额=%.2f, 手续费=%.2f, 新余额=%.2f",
                self.persona_id, trade_value, fees, new_cash
            )
        else:
            # 卖出：增加可用资金
            delta = trade_value - fees
            new_cash = account["available_cash"] + delta
            self.logger.info(
                "[_update_account] 卖出回款: persona=%s, 交易金额=%.2f, 手续费=%.2f, 新余额=%.2f",
                self.persona_id, trade_value, fees, new_cash
            )

        self.brain.store.update_account_balance(
            account_id=account["account_id"],
            available_cash=new_cash,
        )

    def _auto_open_position(self, signal: TradeSignal, fill: Fill) -> None:
        """成交后自动建仓。

        注意: 重复持仓检查已在 _mock_execute() 下单前完成。
        DB 层也有 UNIQUE INDEX (symbol, persona_id) WHERE status='open' 作为兜底。
        """
        position_id = f"POS-{uuid.uuid4().hex[:8].upper()}"
        self.brain.store.open_position(
            position_id=position_id,
            symbol=signal["symbol"],
            entry_price=fill["avg_price"],
            qty=fill["quantity"],
            entry_date=datetime.now().strftime("%Y-%m-%d"),
            name=signal.get("name", ""),
            side="long",
            signal_id=signal["signal_id"],
            thesis=signal.get("rationale", ""),
            strategy=signal.get("strategy", ""),
            bull_case=signal.get("bull_case", ""),
            bear_case=signal.get("bear_case", ""),
            target_price=signal.get("target_price"),
            stop_loss=signal.get("stop_loss"),
            market_regime=signal.get("market_regime", ""),
            persona_version=signal.get("persona_version", ""),
            sector=signal.get("sector", ""),
            persona_id=self.persona_id,
        )
        self.logger.info(
            "[MOCK] 自动建仓 %s | %s | 成本=%.2f | 数量=%d | 策略=%s | regime=%s",
            position_id, signal["symbol"], fill["avg_price"],
            fill["quantity"], signal.get("strategy", ""),
            signal.get("market_regime", "n/a"),
        )

    # ------------------------------------------------------------------
    # Watchlist 桥接 — breakout/pullback 信号 → 自选股池
    # ------------------------------------------------------------------

    def _add_to_watchlist_from_signal(
        self, signal: TradeSignal, condition: str,
    ) -> None:
        """将 breakout/pullback 信号同步到自选股池。

        条件映射:
        - breakout → price_above:entry_price (等突破再买)
        - pullback → price_below:entry_price (等回调再买)
        """
        symbol = signal["symbol"]
        entry_price = signal["entry_price"]

        # 已在自选股中则跳过
        existing_symbols = set(self.brain.store.get_watchlist_symbols())
        if symbol in existing_symbols:
            self.logger.info("[WATCHLIST] %s 已在自选股池中，跳过", symbol)
            return

        # 条件语法映射
        if condition == "breakout":
            entry_cond = f"price_above:{entry_price}"
        else:
            entry_cond = f"price_below:{entry_price}"

        item = {
            "watch_id": f"W-{uuid.uuid4().hex[:8].upper()}",
            "symbol": symbol,
            "name": signal.get("name", ""),
            "sector": signal.get("sector", ""),
            "status": "watching",
            "thesis": signal.get("rationale", ""),
            "entry_condition": entry_cond,
            "target_price": signal.get("target_price"),
            "stop_loss": signal.get("stop_loss"),
            "strategy_id": signal.get("strategy_id", ""),
            "source": f"strategist_{condition}",
            "added_at": datetime.now().isoformat(),
            "last_price": signal.get("current_price"),
        }
        try:
            self.brain.store.add_to_watchlist(item)
            self.logger.info(
                "[WATCHLIST] 加入自选股: %s %s | 条件=%s | 目标=%.2f | 止损=%.2f",
                symbol, signal.get("name", ""), entry_cond,
                signal.get("target_price", 0) or 0,
                signal.get("stop_loss", 0) or 0,
            )
        except Exception:
            self.logger.warning("[WATCHLIST] 加入自选股失败: %s", symbol, exc_info=True)

    async def _shadow_execute(self, signal: TradeSignal) -> tuple[Order, Fill | None]:
        """影子模式执行：双轨运行 mock + 实盘，对比结果。

        1. mock 照常记录到数据库（保持系统一致性）
        2. 同时发送真实订单到券商（通过 BrokerAdapter）
        3. 记录差异到日志和数据库，供后续分析
        """
        # 先执行 mock（保证数据库状态一致）
        order, fill = await self._mock_execute(signal)

        # 同时发送到实盘券商
        try:
            adapter = await self._get_broker_adapter()
            if adapter and adapter.is_connected():
                from src.agents.executioner.broker_adapter import OrderSide, OrderType

                side = OrderSide.BUY if signal.get("action") == "buy" else OrderSide.SELL
                quantity = fill["quantity"] if fill else 100
                price = signal["entry_price"]

                broker_result = await adapter.place_order(
                    symbol=signal["symbol"],
                    side=side,
                    quantity=quantity,
                    price=price,
                    order_type=OrderType.LIMIT,
                )

                self.logger.info(
                    "[SHADOW] 实盘下单结果 | %s %s | 状态=%s | broker_order_id=%s",
                    signal["symbol"], side.value,
                    broker_result.status.value, broker_result.order_id,
                )

                # 记录 shadow 对比到数据库
                self._record_shadow_comparison(signal, fill, broker_result)

                if broker_result.reject_reason:
                    self.logger.warning(
                        "[SHADOW] 实盘被拒绝: %s", broker_result.reject_reason,
                    )
            else:
                self.logger.warning("[SHADOW] Broker 未连接，仅执行 mock")
        except Exception as e:
            self.logger.error("[SHADOW] 实盘下单异常（mock 不受影响）: %s", e)

        return order, fill

    async def _live_execute(self, signal: TradeSignal) -> tuple[Order, Fill | None]:
        """实盘执行：通过 BrokerAdapter 对接真实券商。

        ⚠️ 高风险模式 — 仅在 shadow 模式验证充分后开启。
        与 mock 模式的区别：
        1. 订单由真实券商执行
        2. 成交回报驱动（而非模拟立即成交）
        3. 仓位和资金由券商数据同步
        """
        from src.agents.executioner.broker_adapter import OrderSide, OrderStatus, OrderType

        adapter = await self._get_broker_adapter()
        if not adapter or not adapter.is_connected():
            self.logger.error("[LIVE] Broker 未连接，拒绝执行。请检查 XTQUANT_BRIDGE_URL 配置")
            raise RuntimeError("Broker adapter not connected. Cannot execute in live mode.")

        action = signal.get("action", "")
        symbol = signal["symbol"]
        side = OrderSide.BUY if action == "buy" else OrderSide.SELL

        # 计算下单数量
        if action == "sell" and signal.get("target_qty"):
            quantity = signal["target_qty"]
        else:
            account = self.brain.store.get_account_by_persona(self.persona_id)
            available_cash = account["available_cash"] if account else 10000
            position_pct = signal.get("position_pct", 0.08)
            entry_price = signal["entry_price"]
            allocation = available_cash * position_pct
            quantity = max(100, int(allocation / entry_price / 100) * 100) if entry_price > 0 else 100

        # 发送到券商
        broker_result = await adapter.place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=signal["entry_price"],
            order_type=OrderType.LIMIT,
        )

        order_id = f"ORD-{broker_result.order_id or uuid.uuid4().hex[:8].upper()}"
        now = datetime.now().isoformat()

        order: Order = {
            "order_id": order_id,
            "signal_id": signal["signal_id"],
            "symbol": symbol,
            "side": side.value,
            "quantity": quantity,
            "order_type": "limit",
            "limit_price": signal["entry_price"],
            "status": "submitted" if broker_result.status == OrderStatus.SUBMITTED else "rejected",
            "submitted_at": now,
            "updated_at": now,
        }
        self.brain.store.save_order(order)

        fill: Fill | None = None
        if broker_result.status in (OrderStatus.FILLED, OrderStatus.SUBMITTED):
            # 实盘模式下，成交回报由后台轮询确认（非立即成交）
            # 这里先标记为 submitted，由 _poll_live_orders 定时检查成交
            self.logger.info(
                "[LIVE] ✅ 委托已提交 | %s %s %s | %d × %.2f | broker_id=%s",
                order_id, side.value, symbol, quantity,
                signal["entry_price"], broker_result.order_id,
            )
            # 存储 broker order id 用于后续轮询
            self.brain.store.update_signal_status(signal["signal_id"], "live_submitted")
        else:
            self.logger.warning(
                "[LIVE] ❌ 委托被拒 | %s %s | 原因: %s",
                symbol, side.value, broker_result.reject_reason,
            )
            self.brain.store.update_signal_status(signal["signal_id"], "live_rejected")

        return order, fill

    # ------------------------------------------------------------------
    # Broker Adapter 管理
    # ------------------------------------------------------------------

    _broker_adapter_instance = None

    async def _get_broker_adapter(self):
        """获取或创建 BrokerAdapter 实例（单例）。"""
        if ExecutionEngine._broker_adapter_instance is not None:
            return ExecutionEngine._broker_adapter_instance

        mode = self.mode
        if mode not in ("shadow", "live"):
            return None

        try:
            from src.agents.executioner.broker_adapter import SafeBrokerAdapter, SafetyConfig
            from src.agents.executioner.xtquant_client import XtQuantHttpAdapter

            # 创建底层 adapter
            raw_adapter = XtQuantHttpAdapter()

            # 从配置加载安全参数
            safety_config = SafetyConfig(
                max_single_order_amount=float(cfg().get("max_single_order_amount", 10000)),
                max_daily_buy_amount=float(cfg().get("max_daily_buy_amount", 30000)),
                max_total_position_value=float(cfg().get("max_total_position_value", 50000)),
                max_daily_orders=int(cfg().get("max_daily_orders", 10)),
                max_daily_loss=float(cfg().get("max_daily_loss", 1000)),
            )

            # 包装安全层
            safe_adapter = SafeBrokerAdapter(raw_adapter, safety_config)

            # 连接
            connected = await safe_adapter.connect()
            if connected:
                ExecutionEngine._broker_adapter_instance = safe_adapter
                self.logger.info("BrokerAdapter 初始化成功")
            else:
                self.logger.warning("BrokerAdapter 连接失败，实盘功能不可用")
                return None

            return ExecutionEngine._broker_adapter_instance

        except ImportError as e:
            self.logger.error("BrokerAdapter 依赖缺失: %s", e)
            return None
        except Exception as e:
            self.logger.error("BrokerAdapter 初始化异常: %s", e)
            return None

    def _record_shadow_comparison(self, signal, mock_fill, broker_result) -> None:
        """记录 shadow 模式下 mock 与实盘的对比数据。"""
        try:
            comparison = {
                "signal_id": signal["signal_id"],
                "symbol": signal["symbol"],
                "action": signal.get("action", ""),
                "mock_fill_price": mock_fill["avg_price"] if mock_fill else 0,
                "mock_fill_qty": mock_fill["quantity"] if mock_fill else 0,
                "live_status": broker_result.status.value,
                "live_order_id": broker_result.order_id,
                "live_reject_reason": broker_result.reject_reason,
                "timestamp": datetime.now().isoformat(),
            }
            # 写入日志（后续可扩展为写入专门的 shadow_comparisons 表）
            self.logger.info("[SHADOW COMPARE] %s", comparison)
        except Exception as e:
            self.logger.debug("Shadow 对比记录失败: %s", e)

    def get_portfolio_snapshot(self) -> dict:
        """获取当前持仓快照（优先从 positions 表读取，按 persona 过滤）。"""
        positions = self.brain.store.list_open_positions(persona_id=self.persona_id)
        if positions:
            pos_map = {}
            for p in positions:
                pos_map[p["symbol"]] = {
                    "quantity": p["current_qty"],
                    "avg_cost": p["entry_price"],
                    "total_cost": p["entry_price"] * p["current_qty"],
                }
            total_cost = sum(v["total_cost"] for v in pos_map.values())
            return {
                "cash": cfg().get("initial_capital", 300000) - total_cost,
                "positions": pos_map,
                "position_count": len(positions),
            }

        # Fallback: 从当前 session 的 fills 计算
        fills = self.filled_orders
        pos_map = {}
        for f in fills:
            sym = f["symbol"]
            if sym not in pos_map:
                pos_map[sym] = {"quantity": 0, "avg_cost": 0.0, "total_cost": 0.0}
            pos_map[sym]["quantity"] += f["quantity"]
            pos_map[sym]["total_cost"] += f["quantity"] * f["avg_price"] + f.get("fees", 0)
        for p in pos_map.values():
            if p["quantity"] > 0:
                p["avg_cost"] = round(p["total_cost"] / p["quantity"], 3)
        return {
            "cash": cfg().get("initial_capital", 300000) - sum(p["total_cost"] for p in pos_map.values()),
            "positions": pos_map,
            "fill_count": len(fills),
        }


async def run_execution_node(state: TradingState) -> dict[str, Any]:
    """LangGraph 节点函数 — 执行者盯盘与下单。

    输入：含 trade_signals 的 TradingState
    输出：{"active_orders": [...], "filled_orders": [...], "portfolio_status": {...}}
    """
    session_id = state["session_id"]
    persona_id = state.get("persona_id") or "short_term_hot_rotation_v1"
    engine = ExecutionEngine(session_id, persona_id=persona_id)

    signals = state.get("trade_signals", [])
    if not signals:
        return {
            "active_orders": [],
            "filled_orders": [],
            "portfolio_status": {},
            "logs": state.get("logs", []) + ["[Executioner] 无信号，跳过"],
        }

    orders, fills = await engine.monitor_and_execute(signals)
    portfolio = engine.get_portfolio_snapshot()

    return {
        "active_orders": orders,
        "filled_orders": fills,
        "portfolio_status": portfolio,
        "logs": state.get("logs", []) + [
            f"[Executioner] 成交 {len(fills)} / {len(orders)} 笔, "
            f"持仓 {len(portfolio.get('positions', {}))} 只"
        ],
    }
