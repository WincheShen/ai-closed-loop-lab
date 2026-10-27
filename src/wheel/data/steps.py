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


class WatchlistUltraStep:
    """data.watchlist_ultra — 读取隔夜客短线自选池与最新大盘天气快照（Phase 3.1）。"""

    name = "watchlist_ultra"
    provides = ("candidates", "regime_snapshot")
    param_keys: tuple[str, ...] = ()

    def load(self, ctx: Context, params: dict) -> dict:
        if "candidates" in ctx.data:
            return {
                "candidates": list(ctx.data.get("candidates") or []),
                "regime_snapshot": dict(ctx.data.get("regime_snapshot") or {}),
            }
        if ctx.brain is None:
            return {"candidates": [], "regime_snapshot": {}}
        try:
            from src.agents.explorer.short_term_watchlist import ShortTermWatchlistManager
            items = ShortTermWatchlistManager(ctx.brain).get_active_pool()
        except Exception as e:  # noqa: BLE001
            logger.warning("data.watchlist_ultra 读取自选池失败: %s", e)
            items = []
        regime: dict[str, Any] = {}
        try:
            regime = ctx.brain.store.latest_market_regime() or {}
        except Exception:  # noqa: BLE001
            pass
        return {"candidates": items, "regime_snapshot": regime}


class CandidateIntradayStep:
    """data.candidate_intraday — 拉取单只候选标的 30m 盘中快照与 25 日历史日线特征（Phase 3.1）。"""

    name = "candidate_intraday"
    provides = ("candidate", "quote", "intraday_metrics")
    param_keys: tuple[str, ...] = ("period", "bar_limit", "kline_days")

    def load(self, ctx: Context, params: dict) -> dict:
        cand = dict(ctx.data.get("candidate") or (ctx.event.subject or {}))
        symbol = str(cand.get("symbol") or (ctx.event.subject or {}).get("symbol") or "").strip()
        if not symbol:
            return {}
        cand.setdefault("symbol", symbol)
        name = str(cand.get("name") or symbol)

        if "intraday_metrics" in ctx.data and "quote" in ctx.data:
            return {
                "candidate": cand,
                "quote": ctx.data["quote"],
                "intraday_metrics": ctx.data["intraday_metrics"],
            }

        period = str(params.get("period", "30"))
        bar_limit = int(params.get("bar_limit", 8))
        kline_days = int(params.get("kline_days", 25))

        quote: dict[str, Any] = {"price": 0.0, "is_mock": True, "symbol": symbol}
        metrics: dict[str, Any] = {"valid": False, "reason": "未拉取行情"}

        try:
            from src.stock_analyzer.data_source.akshare_client import AkshareClient
            from src.stock_analyzer.data_source.intraday_client import IntradayClient

            intraday = IntradayClient(allow_mock_fallback=False)
            snapshot = intraday.fetch_intraday_snapshot(
                symbol, name=name, period=period, bar_limit=bar_limit,
            )
            current_price = float(snapshot.current_price or 0.0)
            is_mock = bool(getattr(snapshot, "is_mock", False))
            bars = list(snapshot.bars or [])
            quote = {
                "price": current_price,
                "is_mock": is_mock,
                "symbol": symbol,
                "snapshot": snapshot,
            }
            if is_mock or not bars or current_price <= 0:
                metrics = {"valid": False, "reason": "mock/无数据/价格异常"}
                return {"candidate": cand, "quote": quote, "intraday_metrics": metrics}
            if len(bars) < 2:
                metrics = {"valid": False, "reason": "K线数量不足"}
                return {"candidate": cand, "quote": quote, "intraday_metrics": metrics}

            latest_date = bars[-1].timestamp.date()
            today_bars = [b for b in bars if b.timestamp.date() == latest_date]
            if not today_bars:
                metrics = {"valid": False, "reason": "无当日K线"}
                return {"candidate": cand, "quote": quote, "intraday_metrics": metrics}

            hist_bars = []
            try:
                kline_client = AkshareClient(allow_mock_fallback=False)
                daily_bars = kline_client.fetch_kline(symbol, days=kline_days)
                if daily_bars and not getattr(daily_bars[-1], "is_mock", False):
                    hist_bars = [b for b in daily_bars if b.date < latest_date and b.close > 0]
            except Exception as e:  # noqa: BLE001
                logger.debug("data.candidate_intraday 读取 %s 历史日线异常: %s", symbol, e)

            prev_close = (
                hist_bars[-1].close
                if hist_bars and hist_bars[-1].close > 0
                else float(snapshot.prev_close or 0.0)
            )
            high = max(
                [b.high for b in today_bars]
                + ([snapshot.high] if (snapshot.high or 0) > 0 else [])
            )
            low_candidates = [b.low for b in today_bars if b.low > 0]
            if (snapshot.low or 0) > 0:
                low_candidates.append(snapshot.low)
            low = min(low_candidates) if low_candidates else current_price
            day_range = max(high - low, 0.0)
            close_pos = (current_price - low) / day_range if day_range > 0 else 0.5
            amplitude = day_range / prev_close if prev_close > 0 else 0.0
            turnover = (
                sum(b.turnover for b in today_bars)
                if any(b.turnover > 0 for b in today_bars)
                else float(snapshot.turnover or 0.0)
            )
            change_pct = (
                (current_price / prev_close - 1.0) * 100.0
                if prev_close > 0 and hist_bars
                else float(snapshot.change_pct or 0.0)
            )

            ret5: float | None = None
            ret20: float | None = None
            drawdown: float | None = None
            avg_to_20: float = 0.0
            if len(hist_bars) >= 20:
                ret5 = current_price / hist_bars[-5].close - 1.0 if hist_bars[-5].close > 0 else 0.0
                ret20 = current_price / hist_bars[-20].close - 1.0 if hist_bars[-20].close > 0 else 0.0
                high10 = max(max(b.high for b in hist_bars[-9:]), high)
                drawdown = (high10 - current_price) / high10 if high10 > 0 else 0.0
                avg_to_20 = sum(b.turnover for b in hist_bars[-20:]) / 20.0

            metrics = {
                "valid": True,
                "price": current_price,
                "open": float(snapshot.open or 0.0),
                "high": high,
                "low": low,
                "prev_close": prev_close,
                "day_range": day_range,
                "close_pos": close_pos,
                "amplitude": amplitude,
                "turnover": turnover,
                "change_pct": change_pct,
                "ret5": ret5,
                "ret20": ret20,
                "drawdown": drawdown,
                "avg_to_20": avg_to_20,
                "trade_date": latest_date.isoformat(),
            }
        except Exception as e:  # noqa: BLE001
            metrics = {"valid": False, "reason": f"盘中快照拉取失败: {e}"}

        return {"candidate": cand, "quote": quote, "intraday_metrics": metrics}


class PendingSignalStep:
    """data.pending_signal — 按 subject.signal_id 读取待执行/待触发信号（Phase 3.2 / 3.3）。"""

    name = "pending_signal"
    provides = ("signal",)
    param_keys: tuple[str, ...] = ()

    def load(self, ctx: Context, params: dict) -> dict:
        if "signal" in ctx.data and ctx.data["signal"]:
            return {"signal": ctx.data["signal"]}
        sig_id = (ctx.event.subject or {}).get("signal_id")
        if not sig_id or ctx.brain is None:
            return {}
        try:
            conn = ctx.brain.store._conn()
            row = conn.execute(
                "SELECT * FROM trade_signals WHERE signal_id = ?", (sig_id,)
            ).fetchone()
            return {"signal": dict(row)} if row else {}
        except Exception as e:  # noqa: BLE001
            logger.warning("data.pending_signal 读取 %s 失败: %s", sig_id, e)
            return {}


class AccountMonitorStep:
    """data.account_monitor — 计算当前角色当日盈亏与连续亏损笔数（Phase 4.1）。"""

    name = "account_monitor"
    provides = ("account_risk",)
    param_keys: tuple[str, ...] = ("window_days",)

    def load(self, ctx: Context, params: dict) -> dict:
        if "account_risk" in ctx.data and ctx.data["account_risk"]:
            return {"account_risk": ctx.data["account_risk"]}
        from src.wheel.persona import compute_account_risk_metrics
        window_days = int(params.get("window_days", 14))
        metrics = compute_account_risk_metrics(
            ctx.brain,
            ctx.persona.persona_id or "short_term_hot_rotation_v1",
            now=ctx.now,
            window_days=window_days,
            max_daily_loss_pct=ctx.persona.max_daily_loss_pct,
            consecutive_loss_limit=ctx.persona.consecutive_loss_limit,
        )
        return {"account_risk": metrics}


class ExplorerScanStep:
    """data.explorer_scan — 日度全市场/价值池候选扫描（Phase 3.4）。"""

    name = "explorer_scan"
    provides = ("candidates", "regime_snapshot")
    param_keys: tuple[str, ...] = ()

    def load(self, ctx: Context, params: dict) -> dict:
        if "candidates" in ctx.data:
            return {
                "candidates": list(ctx.data.get("candidates") or []),
                "regime_snapshot": dict(ctx.data.get("regime_snapshot") or {}),
            }
        regime: dict[str, Any] = dict(ctx.data.get("regime_snapshot") or {})
        if not regime and ctx.brain is not None:
            try:
                regime = ctx.brain.store.latest_market_regime() or {}
            except Exception:  # noqa: BLE001
                regime = {}
        candidates: list[dict] = []
        if ctx.mode == "live":
            try:
                from src.agents.explorer.scanner import ExplorerScanner
                scanner = ExplorerScanner(
                    ctx.event.event_id,
                    persona_id=ctx.persona.persona_id or None,
                    market_regime=regime,
                )
                candidates = scanner.scan_market()
            except Exception as e:  # noqa: BLE001
                logger.warning("data.explorer_scan 扫描异常: %s", e)
        return {"candidates": candidates, "regime_snapshot": regime}


class StrategistEvalStep:
    """data.strategist_eval — 单只候选票的策略/LLM 评估结果（Phase 3.4）。"""

    name = "strategist_eval"
    provides = ("candidate", "quote", "strategist_signal")
    param_keys: tuple[str, ...] = ("invoke_llm",)

    def load(self, ctx: Context, params: dict) -> dict:
        cand = dict(ctx.data.get("candidate") or (ctx.event.subject or {}))
        symbol = str(cand.get("symbol") or (ctx.event.subject or {}).get("symbol") or "").strip()
        kline = cand.get("kline_summary") or {}
        price = float(
            cand.get("price")
            or kline.get("current_price")
            or (ctx.data.get("quote") or {}).get("price")
            or 0.0
        )
        quote = dict(ctx.data.get("quote") or {"price": price, "is_mock": price <= 0, "symbol": symbol})

        signals_by_sym = ctx.data.get("signals_by_symbol") or {}
        sig = ctx.data.get("strategist_signal") or signals_by_sym.get(symbol)
        if sig is None and cand and (ctx.mode == "live" or params.get("invoke_llm")):
            try:
                from src.agents.cio.trading_persona import get_persona
                from src.agents.strategist.signal_generator import StrategistEngine
                regime = ctx.data.get("regime_snapshot") or (
                    ctx.brain.store.latest_market_regime() if ctx.brain else {}
                ) or {}
                engine = StrategistEngine(
                    ctx.event.event_id,
                    persona=get_persona(persona_id=ctx.persona.persona_id or None),
                    market_regime=regime,
                )
                sig = engine.analyze_candidate(cand)  # type: ignore[arg-type]
            except Exception as e:  # noqa: BLE001
                logger.warning("data.strategist_eval 分析 %s 异常: %s", symbol, e)
        return {"candidate": cand, "quote": quote, "strategist_signal": sig}


def register(reg) -> None:
    reg.register_data_step(PositionStep())
    reg.register_data_step(QuoteRealtimeStep())
    reg.register_data_step(LlmReviewStep())
    reg.register_data_step(WatchlistUltraStep())
    reg.register_data_step(CandidateIntradayStep())
    reg.register_data_step(PendingSignalStep())
    reg.register_data_step(AccountMonitorStep())
    reg.register_data_step(ExplorerScanStep())
    reg.register_data_step(StrategistEvalStep())
