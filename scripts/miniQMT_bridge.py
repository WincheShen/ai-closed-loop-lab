"""miniQMT Bridge Server — 运行在 Windows 上，为 NAS 提供 HTTP 下单接口。

部署说明:
1. 在 Windows 机器上安装 miniQMT 客户端（东方财富量化交易终端）
2. 登录并保持 miniQMT 运行
3. 安装依赖: pip install fastapi uvicorn xtquant
4. 运行: python miniQMT_bridge.py
5. 配置 NAS 环境变量: XTQUANT_BRIDGE_URL=http://<windows-ip>:9090

API 端点:
    GET  /health          - 健康检查
    POST /order/place     - 下单
    POST /order/cancel    - 撤单
    GET  /order/{id}      - 查询订单
    GET  /orders/today    - 当日委托
    GET  /fills/today     - 当日成交
    GET  /positions       - 持仓
    GET  /balance         - 资金

安全说明:
    - 仅在局域网中运行，不要暴露到公网
    - 使用 BRIDGE_TOKEN 环境变量配置 Bearer Token 认证
    - Bridge 本身不做交易逻辑判断，只做 API 转发
"""

from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# xtquant SDK (仅在 Windows + miniQMT 环境可用)
try:
    from xtquant import xtconstant
    from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
    from xtquant.xttype import StockAccount

    XTQUANT_AVAILABLE = True
except ImportError:
    XTQUANT_AVAILABLE = False
    print("⚠️  xtquant SDK 未安装。Bridge 将运行在模拟模式。")
    print("    安装方法: pip install xtquant")
    print("    或从 miniQMT 安装目录复制 xtquant 包。")

# =============================================================================
# Configuration
# =============================================================================

BRIDGE_HOST = os.getenv("BRIDGE_HOST", "0.0.0.0")
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", "9090"))
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "")  # 为空则不验证

# miniQMT 连接配置
MINI_QMT_PATH = os.getenv("MINI_QMT_PATH", r"D:\国金QMT交易端\userdata_mini")
ACCOUNT_ID = os.getenv("XTQUANT_ACCOUNT_ID", "")
ACCOUNT_TYPE = os.getenv("XTQUANT_ACCOUNT_TYPE", "STOCK")  # STOCK / CREDIT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("miniQMT_bridge")

# =============================================================================
# XtQuant Trader Wrapper
# =============================================================================


class TraderCallback(XtQuantTraderCallback if XTQUANT_AVAILABLE else object):
    """xtquant 回调处理。"""

    def on_disconnected(self):
        log.warning("miniQMT 连接断开！")

    def on_stock_order(self, order):
        log.info("委托回报: %s", order)

    def on_stock_trade(self, trade):
        log.info("成交回报: %s", trade)

    def on_order_error(self, order_error):
        log.error("委托错误: %s", order_error)

    def on_cancel_error(self, cancel_error):
        log.error("撤单错误: %s", cancel_error)


class XtTraderManager:
    """管理 xtquant 连接生命周期。"""

    def __init__(self):
        self.trader: Any = None
        self.account: Any = None
        self.connected = False
        self.session_id = int(time.time())

    def connect(self) -> bool:
        """连接到 miniQMT。"""
        if not XTQUANT_AVAILABLE:
            log.warning("xtquant 不可用，运行在 MOCK 模式")
            self.connected = False
            return False

        try:
            self.trader = XtQuantTrader(MINI_QMT_PATH, self.session_id)
            callback = TraderCallback()
            self.trader.register_callback(callback)
            self.trader.start()

            connect_result = self.trader.connect()
            if connect_result != 0:
                log.error("miniQMT 连接失败，错误码: %d", connect_result)
                return False

            # 订阅账户
            account_type = (
                xtconstant.STOCK_ACCOUNT if ACCOUNT_TYPE == "STOCK"
                else xtconstant.CREDIT_ACCOUNT
            )
            self.account = StockAccount(ACCOUNT_ID, account_type)
            subscribe_result = self.trader.subscribe(self.account)
            if subscribe_result != 0:
                log.error("账户订阅失败，错误码: %d", subscribe_result)
                return False

            self.connected = True
            log.info("✅ miniQMT 连接成功 | 账户: %s", ACCOUNT_ID)
            return True

        except Exception as e:
            log.error("miniQMT 连接异常: %s", e)
            self.connected = False
            return False

    def disconnect(self):
        if self.trader:
            try:
                self.trader.stop()
            except Exception:
                pass
        self.connected = False


# Global trader instance
trader_mgr = XtTraderManager()

# =============================================================================
# FastAPI App
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动/关闭时管理 xtquant 连接。"""
    log.info("Bridge 启动中... miniQMT path=%s, account=%s", MINI_QMT_PATH, ACCOUNT_ID)
    trader_mgr.connect()
    yield
    log.info("Bridge 关闭中...")
    trader_mgr.disconnect()


app = FastAPI(
    title="miniQMT Bridge Server",
    description="为 NAS 上的 AI 交易系统提供 A 股实盘下单接口",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Auth Dependency ---

async def verify_token(authorization: str | None = Header(None)):
    """验证 Bearer Token。"""
    if not BRIDGE_TOKEN:
        return  # 未配置 token 则跳过验证
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization[7:]
    if token != BRIDGE_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")


# =============================================================================
# Request/Response Models
# =============================================================================


class PlaceOrderRequest(BaseModel):
    account_id: str
    symbol: str        # e.g. "600519.SH"
    side: str          # "buy" / "sell"
    quantity: int
    price: float
    order_type: str = "limit"  # "limit" / "market"


class CancelOrderRequest(BaseModel):
    account_id: str
    order_id: str


# =============================================================================
# API Endpoints
# =============================================================================


@app.get("/health")
async def health():
    """健康检查。"""
    return {
        "status": "ok",
        "xt_connected": trader_mgr.connected,
        "xtquant_available": XTQUANT_AVAILABLE,
        "account_id": ACCOUNT_ID,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/order/place", dependencies=[Depends(verify_token)])
async def place_order(req: PlaceOrderRequest):
    """下单。"""
    if not trader_mgr.connected:
        # 未连接时返回模拟结果（方便调试）
        log.warning("miniQMT 未连接，返回模拟下单结果")
        return {
            "success": True,
            "order_id": f"MOCK-{int(time.time())}",
            "message": "MOCK MODE - miniQMT not connected",
        }

    try:
        # 映射 side 到 xtquant 常量
        if req.side == "buy":
            order_type = xtconstant.STOCK_BUY
        elif req.side == "sell":
            order_type = xtconstant.STOCK_SELL
        else:
            return {"success": False, "error": f"Unknown side: {req.side}"}

        # 价格类型
        price_type = (
            xtconstant.FIX_PRICE if req.order_type == "limit"
            else xtconstant.LATEST_PRICE
        )

        # 下单
        order_id = trader_mgr.trader.order_stock(
            trader_mgr.account,
            req.symbol,
            order_type,
            req.quantity,
            price_type,
            req.price,
            strategy_name="ai_lab",
            order_remark=f"ai-lab-{datetime.now().strftime('%H%M%S')}",
        )

        if order_id and order_id > 0:
            log.info(
                "✅ 下单成功 | order_id=%d | %s %s %d × %.2f",
                order_id, req.side, req.symbol, req.quantity, req.price,
            )
            return {"success": True, "order_id": order_id}
        else:
            log.error("下单失败 | 返回: %s", order_id)
            return {"success": False, "error": f"order_stock returned {order_id}"}

    except Exception as e:
        log.error("下单异常: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}


@app.post("/order/cancel", dependencies=[Depends(verify_token)])
async def cancel_order(req: CancelOrderRequest):
    """撤单。"""
    if not trader_mgr.connected:
        return {"success": False, "error": "miniQMT not connected"}

    try:
        result = trader_mgr.trader.cancel_order_stock(
            trader_mgr.account, int(req.order_id)
        )
        success = result == 0
        return {"success": success, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/order/{order_id}", dependencies=[Depends(verify_token)])
async def query_order(order_id: str, account_id: str = ""):
    """查询单个订单。"""
    if not trader_mgr.connected:
        return {"order": {"order_id": order_id, "status": "unknown"}}

    try:
        orders = trader_mgr.trader.query_stock_orders(trader_mgr.account)
        for o in orders:
            if str(o.order_id) == order_id:
                return {"order": _format_order(o)}
        return {"order": {"order_id": order_id, "status": "not_found"}}
    except Exception as e:
        return {"order": {"order_id": order_id, "status": "error", "error": str(e)}}


@app.get("/orders/today", dependencies=[Depends(verify_token)])
async def today_orders(account_id: str = ""):
    """查询当日委托。"""
    if not trader_mgr.connected:
        return {"orders": []}

    try:
        orders = trader_mgr.trader.query_stock_orders(trader_mgr.account)
        return {"orders": [_format_order(o) for o in orders]}
    except Exception as e:
        log.error("查询委托失败: %s", e)
        return {"orders": [], "error": str(e)}


@app.get("/fills/today", dependencies=[Depends(verify_token)])
async def today_fills(account_id: str = ""):
    """查询当日成交。"""
    if not trader_mgr.connected:
        return {"fills": []}

    try:
        trades = trader_mgr.trader.query_stock_trades(trader_mgr.account)
        return {"fills": [_format_trade(t) for t in trades]}
    except Exception as e:
        log.error("查询成交失败: %s", e)
        return {"fills": [], "error": str(e)}


@app.get("/positions", dependencies=[Depends(verify_token)])
async def get_positions(account_id: str = ""):
    """查询持仓。"""
    if not trader_mgr.connected:
        return {"positions": []}

    try:
        positions = trader_mgr.trader.query_stock_positions(trader_mgr.account)
        result = []
        for p in positions:
            if p.volume > 0:  # 只返回有持仓的
                result.append({
                    "symbol": p.stock_code,
                    "name": getattr(p, "stock_name", ""),
                    "quantity": p.volume,
                    "available_qty": p.can_use_volume,
                    "avg_cost": p.avg_price,
                    "current_price": getattr(p, "market_value", 0) / p.volume if p.volume else 0,
                    "market_value": getattr(p, "market_value", 0),
                    "unrealized_pnl": getattr(p, "profit", 0),
                    "pnl_pct": (
                        getattr(p, "profit", 0) / (p.avg_price * p.volume) * 100
                        if p.avg_price * p.volume > 0 else 0
                    ),
                })
        return {"positions": result}
    except Exception as e:
        log.error("查询持仓失败: %s", e)
        return {"positions": [], "error": str(e)}


@app.get("/balance", dependencies=[Depends(verify_token)])
async def get_balance(account_id: str = ""):
    """查询账户资金。"""
    if not trader_mgr.connected:
        return {"balance": {
            "total_assets": 0, "available_cash": 0,
            "frozen_cash": 0, "market_value": 0, "today_pnl": 0,
        }}

    try:
        asset = trader_mgr.trader.query_stock_asset(trader_mgr.account)
        return {"balance": {
            "total_assets": asset.total_asset,
            "available_cash": asset.cash,
            "frozen_cash": asset.frozen_cash,
            "market_value": asset.market_value,
            "today_pnl": getattr(asset, "daily_profit", 0),
        }}
    except Exception as e:
        log.error("查询资金失败: %s", e)
        return {"balance": {}, "error": str(e)}


# =============================================================================
# Helpers
# =============================================================================


def _format_order(order) -> dict:
    """格式化 xtquant 委托对象。"""
    # 状态映射
    status_map = {
        48: "submitted",   # 未报
        49: "submitted",   # 待报
        50: "submitted",   # 已报
        51: "submitted",   # 已报待撤
        52: "partial",     # 部分成交
        53: "cancelled",   # 部撤
        54: "cancelled",   # 已撤
        55: "filled",      # 已成
        56: "rejected",    # 废单
        86: "filled",      # 已成(86)
    }
    side_map = {
        23: "buy",   # STOCK_BUY
        24: "sell",  # STOCK_SELL
    }
    return {
        "order_id": str(order.order_id),
        "symbol": order.stock_code,
        "side": side_map.get(order.order_type, "unknown"),
        "quantity": order.order_volume,
        "price": order.price,
        "filled_qty": order.traded_volume,
        "filled_price": order.traded_price,
        "status": status_map.get(order.order_status, "unknown"),
        "submitted_at": getattr(order, "order_time", ""),
        "reject_reason": getattr(order, "status_msg", ""),
    }


def _format_trade(trade) -> dict:
    """格式化 xtquant 成交对象。"""
    side_map = {23: "buy", 24: "sell"}
    return {
        "order_id": str(trade.order_id),
        "symbol": trade.stock_code,
        "side": side_map.get(trade.order_type, "unknown"),
        "quantity": trade.traded_volume,
        "price": trade.traded_price,
        "filled_qty": trade.traded_volume,
        "filled_price": trade.traded_price,
        "fees": getattr(trade, "commission", 0),
        "filled_at": getattr(trade, "traded_time", ""),
        "status": "filled",
    }


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  miniQMT Bridge Server")
    print(f"  Host: {BRIDGE_HOST}:{BRIDGE_PORT}")
    print(f"  Account: {ACCOUNT_ID or '(未配置)'}")
    print(f"  miniQMT Path: {MINI_QMT_PATH}")
    print(f"  xtquant: {'✅ 可用' if XTQUANT_AVAILABLE else '❌ 不可用 (MOCK模式)'}")
    print(f"  Token Auth: {'✅ 已启用' if BRIDGE_TOKEN else '⚠️ 未启用'}")
    print("=" * 60)

    uvicorn.run(app, host=BRIDGE_HOST, port=BRIDGE_PORT, log_level="info")
