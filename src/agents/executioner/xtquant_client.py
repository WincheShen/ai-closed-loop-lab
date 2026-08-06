"""XtQuant HTTP 客户端 — 通过 HTTP 桥接连接远程 miniQMT。

此 adapter 运行在 NAS (Linux) 上，通过 HTTP 调用 Windows 机器上的
miniQMT Bridge Server (scripts/miniQMT_bridge.py)。

配置项 (.env):
    XTQUANT_BRIDGE_URL=http://192.168.x.x:9090  # Windows 机器 IP
    XTQUANT_BRIDGE_TOKEN=your-secret-token        # 简单认证
    XTQUANT_ACCOUNT_ID=8888888888                 # 资金账号
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import httpx

from src.agents.executioner.broker_adapter import (
    AccountBalance,
    BrokerAdapter,
    BrokerOrder,
    BrokerPosition,
    OrderSide,
    OrderStatus,
    OrderType,
)
from src.infra.config import cfg
from src.infra.logger import get_agent_logger

logger = get_agent_logger("xtquant_client", "init")


class XtQuantHttpAdapter(BrokerAdapter):
    """通过 HTTP 桥接调用 miniQMT xtquant API。

    Bridge Server 需要运行在安装了 miniQMT + xtquant 的 Windows 机器上。
    """

    def __init__(
        self,
        bridge_url: str | None = None,
        bridge_token: str | None = None,
        account_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._bridge_url = (
            bridge_url
            or cfg().get("xtquant_bridge_url")
            or "http://localhost:9090"
        )
        self._token = bridge_token or cfg().get("xtquant_bridge_token") or ""
        self._account_id = account_id or cfg().get("xtquant_account_id") or ""
        self._timeout = timeout
        self._connected = False
        self._client: httpx.AsyncClient | None = None
        self.logger = get_agent_logger("xtquant_client", "http")

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """测试与 Bridge Server 的连接。"""
        try:
            self._client = httpx.AsyncClient(
                base_url=self._bridge_url,
                timeout=self._timeout,
                headers=self._auth_headers(),
            )
            resp = await self._client.get("/health")
            if resp.status_code == 200:
                data = resp.json()
                self._connected = data.get("xt_connected", False)
                self.logger.info(
                    "Bridge 连接成功 | URL=%s | xtquant=%s | account=%s",
                    self._bridge_url,
                    "已连接" if self._connected else "未连接",
                    self._account_id,
                )
                return self._connected
            else:
                self.logger.error("Bridge 健康检查失败: HTTP %d", resp.status_code)
                return False
        except Exception as e:
            self.logger.error("Bridge 连接失败: %s", e)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Order Operations
    # ------------------------------------------------------------------

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        price: float,
        order_type: OrderType = OrderType.LIMIT,
    ) -> BrokerOrder:
        """下单到 miniQMT。"""
        payload = {
            "account_id": self._account_id,
            "symbol": self._normalize_symbol(symbol),
            "side": side.value,
            "quantity": quantity,
            "price": price,
            "order_type": order_type.value,
        }

        self.logger.info(
            "发送下单请求 | %s %s | %d × %.2f | type=%s",
            side.value, symbol, quantity, price, order_type.value,
        )

        resp = await self._request("POST", "/order/place", json=payload)

        if resp.get("success"):
            order_id = str(resp.get("order_id", ""))
            self.logger.info("下单成功 | order_id=%s | %s %s", order_id, side.value, symbol)
            return BrokerOrder(
                order_id=order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                order_type=order_type,
                status=OrderStatus.SUBMITTED,
                submitted_at=datetime.now().isoformat(),
                raw_response=resp,
            )
        else:
            reason = resp.get("error", "unknown error")
            self.logger.error("下单失败 | %s %s | 原因: %s", side.value, symbol, reason)
            return BrokerOrder(
                order_id="",
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                order_type=order_type,
                status=OrderStatus.REJECTED,
                reject_reason=reason,
                submitted_at=datetime.now().isoformat(),
                raw_response=resp,
            )

    async def cancel_order(self, order_id: str) -> bool:
        """撤单。"""
        resp = await self._request("POST", "/order/cancel", json={
            "account_id": self._account_id,
            "order_id": order_id,
        })
        success = resp.get("success", False)
        if success:
            self.logger.info("撤单成功 | order_id=%s", order_id)
        else:
            self.logger.warning("撤单失败 | order_id=%s | %s", order_id, resp.get("error"))
        return success

    async def query_order(self, order_id: str) -> BrokerOrder:
        """查询订单状态。"""
        resp = await self._request("GET", f"/order/{order_id}", params={
            "account_id": self._account_id,
        })
        return self._parse_order(resp.get("order", {}))

    async def get_today_orders(self) -> list[BrokerOrder]:
        """获取当日委托。"""
        resp = await self._request("GET", "/orders/today", params={
            "account_id": self._account_id,
        })
        return [self._parse_order(o) for o in resp.get("orders", [])]

    async def get_today_fills(self) -> list[BrokerOrder]:
        """获取当日成交。"""
        resp = await self._request("GET", "/fills/today", params={
            "account_id": self._account_id,
        })
        return [self._parse_order(o) for o in resp.get("fills", [])]

    # ------------------------------------------------------------------
    # Position & Balance
    # ------------------------------------------------------------------

    async def get_positions(self) -> list[BrokerPosition]:
        """获取持仓列表。"""
        resp = await self._request("GET", "/positions", params={
            "account_id": self._account_id,
        })
        positions = []
        for p in resp.get("positions", []):
            positions.append(BrokerPosition(
                symbol=p.get("symbol", ""),
                name=p.get("name", ""),
                quantity=p.get("quantity", 0),
                available_qty=p.get("available_qty", 0),
                avg_cost=p.get("avg_cost", 0.0),
                current_price=p.get("current_price", 0.0),
                market_value=p.get("market_value", 0.0),
                unrealized_pnl=p.get("unrealized_pnl", 0.0),
                pnl_pct=p.get("pnl_pct", 0.0),
            ))
        return positions

    async def get_balance(self) -> AccountBalance:
        """获取账户资金。"""
        resp = await self._request("GET", "/balance", params={
            "account_id": self._account_id,
        })
        b = resp.get("balance", {})
        return AccountBalance(
            total_assets=b.get("total_assets", 0.0),
            available_cash=b.get("available_cash", 0.0),
            frozen_cash=b.get("frozen_cash", 0.0),
            market_value=b.get("market_value", 0.0),
            today_pnl=b.get("today_pnl", 0.0),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """认证头。"""
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """发送 HTTP 请求到 Bridge。"""
        if not self._client:
            raise RuntimeError("未连接到 Bridge Server，请先调用 connect()")

        try:
            resp = await self._client.request(method, path, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            self.logger.error("Bridge 请求超时: %s %s", method, path)
            raise
        except httpx.HTTPStatusError as e:
            self.logger.error("Bridge HTTP 错误: %s %s → %d", method, path, e.response.status_code)
            return {"success": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            self.logger.error("Bridge 请求异常: %s %s → %s", method, path, e)
            self._connected = False
            raise

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """标准化股票代码为 xtquant 格式。

        输入: '600519' 或 '000001'
        输出: '600519.SH' 或 '000001.SZ'
        """
        # 已经有后缀的直接返回
        if "." in symbol:
            return symbol

        code = symbol.strip()
        if code.startswith(("6", "5", "9")):
            return f"{code}.SH"
        elif code.startswith(("0", "3", "1", "2")):
            return f"{code}.SZ"
        elif code.startswith("4") or code.startswith("8"):
            return f"{code}.BJ"
        else:
            return f"{code}.SH"

    @staticmethod
    def _parse_order(data: dict) -> BrokerOrder:
        """解析 Bridge 返回的订单数据。"""
        status_map = {
            "pending": OrderStatus.PENDING,
            "submitted": OrderStatus.SUBMITTED,
            "partial": OrderStatus.PARTIAL_FILLED,
            "filled": OrderStatus.FILLED,
            "cancelled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
            "error": OrderStatus.ERROR,
        }
        return BrokerOrder(
            order_id=str(data.get("order_id", "")),
            symbol=data.get("symbol", ""),
            side=OrderSide(data.get("side", "buy")),
            quantity=data.get("quantity", 0),
            price=data.get("price", 0.0),
            order_type=OrderType(data.get("order_type", "limit")),
            status=status_map.get(data.get("status", ""), OrderStatus.ERROR),
            filled_qty=data.get("filled_qty", 0),
            filled_price=data.get("filled_price", 0.0),
            fees=data.get("fees", 0.0),
            submitted_at=data.get("submitted_at", ""),
            filled_at=data.get("filled_at", ""),
            reject_reason=data.get("reject_reason", ""),
            raw_response=data,
        )
