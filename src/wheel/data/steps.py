"""data.* — 取数步骤（DataStep）。

与 Factor 的区别：可以发网络请求、读库，结果写进 Context.data 供判断步骤使用。
回测时替换为历史数据实现，Factor 无需改动。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from src.wheel.protocol import Context

logger = logging.getLogger(__name__)


class PositionStep:
    """data.position — 按 subject.position_id 取数据库最新持仓。"""

    name = "position"
    provides = ("position",)
    param_keys: tuple[str, ...] = ()

    def load(self, ctx: Context, params: dict) -> dict:
        position_id = (ctx.event.subject or {}).get("position_id")
        if not position_id:
            return {}
        try:
            pos = ctx.brain.store.get_position(position_id)
        except Exception:  # noqa: BLE001
            logger.warning("data.position 读取 %s 失败", position_id, exc_info=True)
            return {}
        return {"position": pos} if pos else {}


class QuoteRealtimeStep:
    """data.quote_realtime — 实时行情（禁 mock 成交的调用方传 allow_mock_fallback=False）。

    优先按 ctx.brain 会话隔离分钟级行情缓存（避免单测/回测回放相同时间戳跨实例污染）；
    同时兼容测试向类属性 _cache 预置的行情。
    """

    name = "quote_realtime"
    provides = ("quote",)
    param_keys: tuple[str, ...] = ("period", "bar_limit")
    _cache: dict[str, dict] = {}
    _lock = threading.Lock()

    @classmethod
    def _session_cache(cls, brain: Any) -> dict[str, dict]:
        if brain is None:
            return cls._cache
        cache = getattr(brain, "_wheel_quote_cache", None)
        if not isinstance(cache, dict):
            try:
                cache = {}
                setattr(brain, "_wheel_quote_cache", cache)
            except Exception:  # noqa: BLE001
                return cls._cache
        return cache

    def load(self, ctx: Context, params: dict) -> dict:
        preloaded = ctx.data.get("quote")
        if preloaded:
            return {"quote": preloaded}

        pos = ctx.data.get("position") or {}
        symbol = pos.get("symbol") or (ctx.event.subject or {}).get("symbol")
        if not symbol:
            return {}

        cache_key = f"{symbol}:{ctx.now.strftime('%Y%m%d%H%M')}"
        session_cache = self._session_cache(ctx.brain)
        with self._lock:
            if cache_key in self._cache:
                return {"quote": self._cache[cache_key]}
            if cache_key in session_cache:
                return {"quote": session_cache[cache_key]}

        period = str(params.get("period", "1"))
        bar_limit = int(params.get("bar_limit", 1))
        quote: dict[str, Any] = {"price": 0.0, "is_mock": True, "symbol": symbol}
        try:
            from src.stock_analyzer.data_source.intraday_client import IntradayClient
            snap = IntradayClient(allow_mock_fallback=False).fetch_intraday_snapshot(
                symbol, name=pos.get("name", ""), period=period, bar_limit=bar_limit,
            )
            quote = {
                "price": float(snap.current_price or 0),
                "is_mock": bool(getattr(snap, "is_mock", False)),
                "symbol": symbol,
                "snapshot": snap,
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("data.quote_realtime %s 拉取失败: %s", symbol, e)

        with self._lock:
            if len(session_cache) > 512:  # 简单防膨胀
                session_cache.clear()
            session_cache[cache_key] = quote
        return {"quote": quote}


class LlmReviewStep:
    """data.llm_review — LLM 复审原始结果。

    影子模式下由调用方通过 preloaded["llm_review"] 注入旧路径已算好的复审，
    避免为同一持仓重复调用 LLM。当无 preloaded 注入且处于 live 模式（或显式
    传 invoke_llm=1）时，按单持仓调用 PositionReviewAgent.review_position(pos)。
    """

    name = "llm_review"
    provides = ("llm_review",)
    param_keys: tuple[str, ...] = ("force_review_reason", "invoke_llm")

    def load(self, ctx: Context, params: dict) -> dict:
        review = ctx.data.get("llm_review")
        if review:
            return {"llm_review": review}
        pos = ctx.data.get("position")
        if pos and (ctx.mode == "live" or params.get("invoke_llm")):
            try:
                from src.agents.reviewer.position_reviewer import PositionReviewAgent

                reason = (
                    params.get("force_review_reason")
                    or (ctx.event.payload or {}).get("reason")
                )
                agent = PositionReviewAgent(persona_id=ctx.persona.persona_id or None)
                if ctx.brain is not None:
                    agent.brain = ctx.brain
                review = agent.review_position(pos, force_review_reason=reason)
                if review:
                    return {"llm_review": review}
            except Exception as e:  # noqa: BLE001
                logger.error("data.llm_review 调用 PositionReviewAgent 失败: %s", e, exc_info=True)
        return {"llm_review": {"action": "HOLD", "reason": "无 LLM 复审输入"}}


def register(reg) -> None:
    reg.register_data_step(PositionStep())
    reg.register_data_step(QuoteRealtimeStep())
    reg.register_data_step(LlmReviewStep())
