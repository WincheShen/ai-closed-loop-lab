"""Social Media Dispatcher — 把 Stock Analyzer / Webhook 输出推送给 SMA。

设计原则（对应需求 FR-2.1 不主动搜热点）：
- 选题输入只来自 daily_picks 或 trade_record
- 通过 HTTP 推送到 Social-media-automation 的 /api/tasks
"""
from .schemas import TopicPayload, TopicContext, DispatchResult
from .topic_router import TopicRouter
from .client import SmaClient, SmaClientError

__all__ = [
    "TopicPayload",
    "TopicContext",
    "DispatchResult",
    "TopicRouter",
    "SmaClient",
    "SmaClientError",
]
