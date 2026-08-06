"""Broker Adapter 抽象层 — 券商接口统一接口。

架构：
┌─────────────────────────────────┐
│ SafeBrokerAdapter (安全护栏)     │
│   ├─ 最大单笔金额限制            │
│   ├─ 日交易次数/金额限制         │
│   ├─ 交易时间窗口检查            │
│   ├─ 持仓总市值上限              │
│   └─ 熔断开关                    │
└─────────────┬───────────────────┘
              │ delegates to
┌─────────────▼───────────────────┐
│ Concrete Adapter                 │
│   - XtQuantHttpAdapter (远程桥)  │
│   - XtQuantLocalAdapter (本地)   │
│   - MockAdapter (测试)           │
└─────────────────────────────────┘
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from src.infra.logger import get_agent_logger

logger = get_agent_logger("broker_adapter", "init")


# =============================================================================
# Data Models
# =============================================================================


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"


class OrderStatus(str, Enum):
    PENDING = "pending"           # 已提交，待确认
    SUBMITTED = "submitted"       # 券商已接收
    PARTIAL_FILLED = "partial"    # 部分成交
    FILLED = "filled"             # 全部成交
    CANCELLED = "cancelled"       # 已撤单
    REJECTED = "rejected"         # 被拒绝
    ERROR = "error"               # 异常


@dataclass
class BrokerOrder:
    """券商订单。"""
    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    order_type: OrderType = OrderType.LIMIT
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    filled_price: float = 0.0
    fees: float = 0.0
    submitted_at: str = ""
    filled_at: str = ""
    reject_reason: str = ""
    raw_response: dict = field(default_factory=dict)


@dataclass
class BrokerPosition:
    """券商持仓。"""
    symbol: str
    name: str
    quantity: int
    available_qty: int       # 可卖数量（T+1 限制）
    avg_cost: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    pnl_pct: float


@dataclass
class AccountBalance:
    """账户资金。"""
    total_assets: float      # 总资产
    available_cash: float    # 可用资金
    frozen_cash: float       # 冻结资金
    market_value: float      # 持仓市值
    today_pnl: float         # 当日盈亏


# =============================================================================
# Abstract Adapter
# =============================================================================


class BrokerAdapter(ABC):
    """券商接口抽象基类。所有券商实现必须继承此类。"""

    @abstractmethod
    async def connect(self) -> bool:
        """建立连接。返回是否成功。"""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接。"""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """是否已连接。"""
        ...

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        price: float,
        order_type: OrderType = OrderType.LIMIT,
    ) -> BrokerOrder:
        """下单。返回订单结果。"""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """撤单。返回是否成功。"""
        ...

    @abstractmethod
    async def query_order(self, order_id: str) -> BrokerOrder:
        """查询订单状态。"""
        ...

    @abstractmethod
    async def get_positions(self) -> list[BrokerPosition]:
        """获取当前持仓。"""
        ...

    @abstractmethod
    async def get_balance(self) -> AccountBalance:
        """获取账户资金。"""
        ...

    @abstractmethod
    async def get_today_orders(self) -> list[BrokerOrder]:
        """获取当日委托。"""
        ...

    @abstractmethod
    async def get_today_fills(self) -> list[BrokerOrder]:
        """获取当日成交。"""
        ...


# =============================================================================
# Safety Wrapper
# =============================================================================


@dataclass
class SafetyConfig:
    """安全护栏配置。"""
    # 单笔限制
    max_single_order_amount: float = 10000.0    # 单笔最大金额（元）
    max_single_order_qty: int = 5000            # 单笔最大股数

    # 日限制
    max_daily_orders: int = 10                  # 每日最大下单数
    max_daily_buy_amount: float = 30000.0       # 每日最大买入金额

    # 持仓限制
    max_total_position_value: float = 50000.0   # 持仓总市值上限
    max_position_per_stock: float = 15000.0     # 单只股票最大持仓

    # 时间限制（A 股交易时间）
    trading_hours: list[tuple[str, str]] = field(
        default_factory=lambda: [("09:30", "11:30"), ("13:00", "14:57")]
    )

    # 黑名单/白名单
    symbol_whitelist: list[str] = field(default_factory=list)  # 空=不限制
    symbol_blacklist: list[str] = field(default_factory=list)

    # 熔断
    kill_switch: bool = False                   # 紧急停止所有交易
    max_daily_loss: float = 1000.0              # 日亏损熔断（元）


class SafeBrokerAdapter(BrokerAdapter):
    """安全包装器 — 在真实下单前执行所有安全检查。

    用法:
        raw_adapter = XtQuantHttpAdapter(...)
        safe_adapter = SafeBrokerAdapter(raw_adapter, SafetyConfig(...))
        await safe_adapter.place_order(...)
    """

    def __init__(self, inner: BrokerAdapter, config: SafetyConfig | None = None) -> None:
        self._inner = inner
        self.config = config or SafetyConfig()
        self._daily_orders: list[dict] = []
        self._daily_reset_date: str = ""
        self.logger = get_agent_logger("broker_safety", "guard")

    # --- Delegate non-write operations directly ---

    async def connect(self) -> bool:
        return await self._inner.connect()

    async def disconnect(self) -> None:
        await self._inner.disconnect()

    def is_connected(self) -> bool:
        return self._inner.is_connected()

    async def cancel_order(self, order_id: str) -> bool:
        return await self._inner.cancel_order(order_id)

    async def query_order(self, order_id: str) -> BrokerOrder:
        return await self._inner.query_order(order_id)

    async def get_positions(self) -> list[BrokerPosition]:
        return await self._inner.get_positions()

    async def get_balance(self) -> AccountBalance:
        return await self._inner.get_balance()

    async def get_today_orders(self) -> list[BrokerOrder]:
        return await self._inner.get_today_orders()

    async def get_today_fills(self) -> list[BrokerOrder]:
        return await self._inner.get_today_fills()

    # --- Guarded write operation ---

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        price: float,
        order_type: OrderType = OrderType.LIMIT,
    ) -> BrokerOrder:
        """下单前执行安全检查。"""
        self._reset_daily_if_needed()

        # 1. Kill switch
        if self.config.kill_switch:
            return self._reject(symbol, side, quantity, price, "KILL_SWITCH 已启用，拒绝所有交易")

        # 2. 交易时间检查
        if not self._in_trading_hours():
            return self._reject(symbol, side, quantity, price, "当前非交易时间")

        # 3. 黑白名单
        if self.config.symbol_whitelist and symbol not in self.config.symbol_whitelist:
            return self._reject(symbol, side, quantity, price, f"标的 {symbol} 不在白名单中")
        if symbol in self.config.symbol_blacklist:
            return self._reject(symbol, side, quantity, price, f"标的 {symbol} 在黑名单中")

        order_amount = quantity * price

        # 以下检查仅针对买入
        if side == OrderSide.BUY:
            # 4. 单笔金额限制
            if order_amount > self.config.max_single_order_amount:
                return self._reject(
                    symbol, side, quantity, price,
                    f"单笔金额 {order_amount:.0f} 超过限制 {self.config.max_single_order_amount:.0f}"
                )

            # 5. 单笔股数限制
            if quantity > self.config.max_single_order_qty:
                return self._reject(
                    symbol, side, quantity, price,
                    f"单笔股数 {quantity} 超过限制 {self.config.max_single_order_qty}"
                )

            # 6. 日下单次数
            daily_buy_count = sum(1 for o in self._daily_orders if o["side"] == "buy")
            if daily_buy_count >= self.config.max_daily_orders:
                return self._reject(
                    symbol, side, quantity, price,
                    f"日下单次数 {daily_buy_count} 已达上限 {self.config.max_daily_orders}"
                )

            # 7. 日买入金额
            daily_buy_amount = sum(
                o["amount"] for o in self._daily_orders if o["side"] == "buy"
            )
            if daily_buy_amount + order_amount > self.config.max_daily_buy_amount:
                return self._reject(
                    symbol, side, quantity, price,
                    f"日买入金额 {daily_buy_amount + order_amount:.0f} 超过限制 {self.config.max_daily_buy_amount:.0f}"
                )

            # 8. 持仓总市值检查
            try:
                positions = await self._inner.get_positions()
                total_mv = sum(p.market_value for p in positions)
                if total_mv + order_amount > self.config.max_total_position_value:
                    return self._reject(
                        symbol, side, quantity, price,
                        f"持仓总市值 {total_mv:.0f} + 本笔 {order_amount:.0f} 超过限制 {self.config.max_total_position_value:.0f}"
                    )

                # 9. 单股持仓限制
                stock_mv = sum(p.market_value for p in positions if p.symbol == symbol)
                if stock_mv + order_amount > self.config.max_position_per_stock:
                    return self._reject(
                        symbol, side, quantity, price,
                        f"单股 {symbol} 持仓 {stock_mv:.0f} + 本笔 {order_amount:.0f} 超过限制 {self.config.max_position_per_stock:.0f}"
                    )
            except Exception as e:
                self.logger.error("持仓查询失败，安全侧拒绝: %s", e)
                return self._reject(symbol, side, quantity, price, f"持仓查询失败: {e}")

            # 10. 日亏损熔断
            try:
                balance = await self._inner.get_balance()
                if balance.today_pnl < -self.config.max_daily_loss:
                    return self._reject(
                        symbol, side, quantity, price,
                        f"日亏损 {balance.today_pnl:.0f} 超过熔断线 {-self.config.max_daily_loss:.0f}"
                    )
            except Exception:
                pass  # 查询失败不阻止

        # ✅ 所有检查通过，执行真实下单
        self.logger.info(
            "✅ 安全检查通过 | %s %s | %d 股 × %.2f = %.0f 元",
            side.value, symbol, quantity, price, order_amount,
        )

        result = await self._inner.place_order(symbol, side, quantity, price, order_type)

        # 记录今日订单
        if result.status not in (OrderStatus.REJECTED, OrderStatus.ERROR):
            self._daily_orders.append({
                "symbol": symbol,
                "side": side.value,
                "quantity": quantity,
                "price": price,
                "amount": order_amount,
                "time": datetime.now().isoformat(),
            })

        return result

    # --- Helpers ---

    def _in_trading_hours(self) -> bool:
        """检查当前是否在交易时间内。"""
        now = datetime.now()
        # 周末不交易
        if now.weekday() >= 5:
            return False

        current_time = now.strftime("%H:%M")
        for start, end in self.config.trading_hours:
            if start <= current_time <= end:
                return True
        return False

    def _reset_daily_if_needed(self) -> None:
        """日期切换时重置每日计数器。"""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._daily_reset_date != today:
            self._daily_orders = []
            self._daily_reset_date = today

    def _reject(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        price: float,
        reason: str,
    ) -> BrokerOrder:
        """生成拒绝订单。"""
        self.logger.warning(
            "🚫 安全护栏拒绝 | %s %s | %d × %.2f | 原因: %s",
            side.value, symbol, quantity, price, reason,
        )
        return BrokerOrder(
            order_id="",
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            status=OrderStatus.REJECTED,
            reject_reason=f"[SAFETY] {reason}",
            submitted_at=datetime.now().isoformat(),
        )
