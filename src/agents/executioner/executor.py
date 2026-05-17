"""Execution Engine — 执行者核心实现。

职责：
1. 接收 TradeSignal，按交易模式执行下单
2. 成交后自动创建 Position 记录（fills → positions 桥接）
3. 严格隔离：mock / paper / live

⚠️ 安全设计：
- TRADING_MODE=mock 时只记录到数据库，不对接券商
- TRADING_MODE=paper 时对接模拟盘（待实现，降级为 mock）
- TRADING_MODE=live 时才走真实接口，且单笔仓位严格限制
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from src.central_brain import get_central_brain
from src.graph.state import Fill, Order, TradeSignal, TradingState
from src.infra.config import cfg
from src.infra.logger import get_agent_logger

logger = get_agent_logger("executioner", "init")


class ExecutionEngine:
    """执行者引擎。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.logger = get_agent_logger("executioner", session_id)
        self.brain = get_central_brain()
        self.mode = cfg().get("trading_mode", "mock")
        self.submitted_orders: list[Order] = []
        self.filled_orders: list[Fill] = []

    async def monitor_and_execute(
        self, signals: list[TradeSignal],
    ) -> tuple[list[Order], list[Fill]]:
        """主循环：监控信号并执行。

        当前为简化版：直接模拟成交（模拟盘模式）。
        后续接入 EasyTrader + WebSocket 实时行情。
        """
        self.logger.info("开始盯盘 — 模式=%s, 信号数=%d", self.mode, len(signals))

        for sig in signals:
            if self.mode == "mock":
                order, fill = await self._mock_execute(sig)
            elif self.mode == "paper":
                order, fill = await self._paper_execute(sig)
            else:
                order, fill = await self._live_execute(sig)

            self.submitted_orders.append(order)
            if fill:
                self.filled_orders.append(fill)
                self.brain.store.update_signal_status(sig["signal_id"], "filled")

        self.logger.info(
            "执行完成 — 提交 %d 笔, 成交 %d 笔",
            len(self.submitted_orders), len(self.filled_orders),
        )
        return self.submitted_orders, self.filled_orders

    async def _mock_execute(self, signal: TradeSignal) -> tuple[Order, Fill | None]:
        """模拟执行：按信号价格成交，并自动建仓。"""
        order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now().isoformat()

        # 动态计算下单数量（基于资金和仓位比例，100 股整数倍）
        capital = cfg().get("initial_capital", 300000)
        position_pct = signal.get("position_pct", 0.08)
        allocation = capital * position_pct
        entry_price = signal["entry_price"]
        quantity = max(100, int(allocation / entry_price / 100) * 100) if entry_price > 0 else 100

        order: Order = {
            "order_id": order_id,
            "signal_id": signal["signal_id"],
            "symbol": signal["symbol"],
            "side": "buy" if signal["action"] == "buy" else "sell",
            "quantity": quantity,
            "order_type": "limit",
            "limit_price": entry_price,
            "status": "submitted",
            "submitted_at": now,
            "updated_at": now,
        }
        self.brain.store.save_order(order)

        self.logger.info(
            "[MOCK] 模拟下单 %s | %s %s | 限价 %.2f | 数量 %d",
            order_id, signal["symbol"], order["side"], entry_price, quantity,
        )

        # 模拟立即成交
        await asyncio.sleep(0.1)
        fill: Fill = {
            "fill_id": f"FIL-{uuid.uuid4().hex[:8].upper()}",
            "order_id": order_id,
            "symbol": signal["symbol"],
            "side": order["side"],
            "quantity": quantity,
            "avg_price": entry_price,
            "fees": round(quantity * entry_price * 0.0003, 2),
            "filled_at": datetime.now().isoformat(),
        }
        self.brain.store.save_fill(fill)

        order["status"] = "filled"
        order["updated_at"] = fill["filled_at"]
        self.brain.store.save_order(order)

        self.brain.bus.emit_order_fill(fill)

        # --- 桥接: BUY 成交 → 自动创建 Position 记录 ---
        if signal["action"] == "buy":
            self._auto_open_position(signal, fill)

        return order, fill

    def _auto_open_position(self, signal: TradeSignal, fill: Fill) -> None:
        """成交后自动建仓（仅当该 symbol 无已有持仓时）。"""
        existing = self.brain.store.list_open_positions()
        if any(p["symbol"] == signal["symbol"] for p in existing):
            self.logger.info(
                "[MOCK] %s 已有持仓，跳过自动建仓", signal["symbol"],
            )
            return

        position_id = f"POS-{uuid.uuid4().hex[:8].upper()}"
        self.brain.store.open_position(
            position_id=position_id,
            symbol=signal["symbol"],
            entry_price=fill["avg_price"],
            qty=fill["quantity"],
            entry_date=datetime.now().strftime("%Y-%m-%d"),
            name=signal.get("name", ""),  # type: ignore[arg-type]
            side="long",
            signal_id=signal["signal_id"],
            thesis=signal.get("rationale", ""),
            strategy=signal.get("strategy", ""),
            bull_case=signal.get("bull_case", ""),  # type: ignore[arg-type]
            bear_case=signal.get("bear_case", ""),  # type: ignore[arg-type]
            target_price=signal.get("target_price"),
            stop_loss=signal.get("stop_loss"),
            # Cognitive Agent Phase 1 元数据
            market_regime=signal.get("market_regime", ""),  # type: ignore[arg-type]
            persona_version=signal.get("persona_version", ""),  # type: ignore[arg-type]
            sector=signal.get("sector", ""),  # type: ignore[arg-type]
        )
        self.logger.info(
            "[MOCK] 自动建仓 %s | %s | 成本=%.2f | 数量=%d | 策略=%s | regime=%s",
            position_id, signal["symbol"], fill["avg_price"],
            fill["quantity"], signal.get("strategy", ""),
            signal.get("market_regime", "n/a"),  # type: ignore[arg-type]
        )

    async def _paper_execute(self, signal: TradeSignal) -> tuple[Order, Fill | None]:
        """模拟盘执行：对接券商模拟盘接口（待实现）。"""
        self.logger.warning("Paper trading 接口尚未接入，降级为 mock 模式")
        return await self._mock_execute(signal)

    async def _live_execute(self, signal: TradeSignal) -> tuple[Order, Fill | None]:
        """实盘执行：对接真实券商接口（⚠️ 高风险，需充分测试后开启）。"""
        self.logger.error("实盘模式已配置但接口尚未接入 — 拒绝执行")
        raise RuntimeError("Live trading API not implemented yet. Set TRADING_MODE=mock or paper.")

    def get_portfolio_snapshot(self) -> dict:
        """获取当前持仓快照（优先从 positions 表读取）。"""
        positions = self.brain.store.list_open_positions()
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
    engine = ExecutionEngine(session_id)

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
