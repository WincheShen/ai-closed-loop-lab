"""TradingAgent Service — 长期运行的单股深度分析 HTTP 服务。

模块布局：
- api/      FastAPI 路由层
- cache/    SQLite 缓存层（含失效策略）
- analysis/ 调用 tradingagents_neo（带 mock fallback）
"""

__version__ = "0.1.0"
