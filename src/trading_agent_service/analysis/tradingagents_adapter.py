"""TradingAgents (real) Adapter — 接入 /Users/neo/Projects/tradingAgents_neo。

设计要点：
- TradingAgentsGraph 初始化代价高（加载 LLM、构建 LangGraph），singleton 复用
- propagate(symbol, date) 返回的 final_state 字段丰富但是文本，需要 mapping 到 Report
- 数值类字段（current_price / pe / pb / market_cap）通过 akshare 单独拉，不走 TradingAgents
- LLM 失败时抛异常，由上层 cache_manager 决定降级

Env 变量：
    OPENAI_API_KEY              （透传给 tradingagents）
    OPENAI_BASE_URL             （透传，默认 https://api.openai.com/v1）
    TAS_TA_DEEP_MODEL           深度思考模型，默认 'gpt-5.3-chat'
    TAS_TA_QUICK_MODEL          快速思考模型，默认同上
    TAS_TA_MARKET               'cn' 或 'us'，默认 'cn'
    TAS_TA_DEBATE_ROUNDS        辩论轮数，默认 1
    TAS_TA_RISK_ROUNDS          风险讨论轮数，默认 1
    TAS_TA_OUTPUT_LANG          报告语言，默认 'Chinese'
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import date, timedelta
from typing import Any, Literal, Optional

from ..api.schemas import (
    FundamentalAnalysis,
    Report,
    TechnicalAnalysis,
)
from .adapter import AnalyzerAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Decision mapping (TradingAgents → our schema)
# ---------------------------------------------------------------------------

_DECISION_MAP: dict[str, Literal["BUY", "HOLD", "SELL"]] = {
    "BUY": "BUY",
    "OVERWEIGHT": "BUY",
    "HOLD": "HOLD",
    "UNDERWEIGHT": "SELL",
    "SELL": "SELL",
}

_CONFIDENCE_BY_DECISION = {
    "BUY": 0.70,
    "OVERWEIGHT": 0.62,
    "HOLD": 0.50,
    "UNDERWEIGHT": 0.62,
    "SELL": 0.70,
}


def _normalize_decision(raw: str) -> tuple[Literal["BUY", "HOLD", "SELL"], float]:
    """从 process_signal 输出（'BUY' / 'HOLD' / ...）映射为 schema 三态 + 默认置信。"""
    upper = (raw or "").strip().upper()
    # 容错：有时模型输出会带句号、引号、解释
    match = re.search(r"\b(BUY|OVERWEIGHT|HOLD|UNDERWEIGHT|SELL)\b", upper)
    key = match.group(1) if match else "HOLD"
    return _DECISION_MAP.get(key, "HOLD"), _CONFIDENCE_BY_DECISION.get(key, 0.5)


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


# ---------------------------------------------------------------------------
# TradingAgentsGraph singleton
# ---------------------------------------------------------------------------

_graph_lock = threading.Lock()
_cached_graph: Any = None


def _build_config() -> dict:
    """Build config dict for TradingAgentsGraph."""
    from tradingagents.default_config import DEFAULT_CONFIG  # type: ignore

    cfg = DEFAULT_CONFIG.copy()
    cfg["market"] = os.getenv("TAS_TA_MARKET", "cn")
    cfg["deep_think_llm"] = os.getenv("TAS_TA_DEEP_MODEL", cfg["deep_think_llm"])
    cfg["quick_think_llm"] = os.getenv("TAS_TA_QUICK_MODEL", cfg["quick_think_llm"])
    cfg["max_debate_rounds"] = int(os.getenv("TAS_TA_DEBATE_ROUNDS", "1"))
    cfg["max_risk_discuss_rounds"] = int(os.getenv("TAS_TA_RISK_ROUNDS", "1"))
    cfg["output_language"] = os.getenv("TAS_TA_OUTPUT_LANG", "Chinese")

    # 关键：market=cn 时自动把 vendor 切换到 akshare
    try:
        from tradingagents.default_config import apply_market_defaults  # type: ignore
        cfg = apply_market_defaults(cfg)
    except ImportError:
        pass
    return cfg


def _get_graph() -> Any:
    """Lazy singleton getter for TradingAgentsGraph."""
    global _cached_graph
    if _cached_graph is not None:
        return _cached_graph
    with _graph_lock:
        if _cached_graph is not None:
            return _cached_graph
        from tradingagents.graph.trading_graph import TradingAgentsGraph  # type: ignore

        cfg = _build_config()
        logger.info(
            "Initializing TradingAgentsGraph: market=%s deep=%s quick=%s rounds=%s",
            cfg.get("market"), cfg.get("deep_think_llm"),
            cfg.get("quick_think_llm"), cfg.get("max_debate_rounds"),
        )
        _cached_graph = TradingAgentsGraph(debug=False, config=cfg)
        return _cached_graph


def reset_graph() -> None:
    """Mainly for tests — reset the singleton."""
    global _cached_graph
    with _graph_lock:
        _cached_graph = None


# ---------------------------------------------------------------------------
# Stock metadata loader（拉数值字段，不走 LLM）
# ---------------------------------------------------------------------------

def _load_stock_meta(symbol: str) -> dict:
    """从 akshare 拉行情 + 基本面，组装数值字段。

    失败不抛异常，返回带默认值的字典。
    """
    out = {
        "name": f"代码{symbol}",
        "current_price": 0.0,
        "industry": "",
        "market_cap_yi": None,
        "pe_ttm": None,
        "pb": None,
        "roe": None,
    }
    try:
        from stock_analyzer.data_source import AkshareClient
    except ImportError:
        logger.warning("stock_analyzer not on path; cannot load stock meta")
        return out

    try:
        client = AkshareClient(allow_mock_fallback=True)
        snap = client.fetch_snapshot()
        match = next(
            (s for s in snap.stocks if s.symbol == symbol or s.symbol.endswith(symbol)),
            None,
        )
        if match is not None:
            out.update({
                "name": match.name,
                "current_price": match.price,
                "industry": match.industry or "",
                "market_cap_yi": match.market_cap_yi,
                "pe_ttm": match.pe_ttm,
                "pb": match.pb,
            })
    except Exception as e:  # noqa: BLE001
        logger.warning("akshare meta load failed for %s: %s", symbol, e)
    return out


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class RealTradingAgentsAdapter(AnalyzerAdapter):
    """调用 tradingAgents_neo 包做真实分析。"""

    name = "tradingagents"

    def __init__(self) -> None:
        try:
            import tradingagents  # type: ignore  # noqa: F401
            self.available = True
        except ImportError:
            logger.warning(
                "tradingagents 包未安装。安装方式：\n"
                "  pip install -e /Users/neo/Projects/tradingAgents_neo"
            )
            self.available = False

    def analyze(
        self,
        symbol: str,
        depth: Literal["deep", "quick"] = "deep",
    ) -> Report:
        if not self.available:
            raise RuntimeError("tradingagents 不可用，需先 pip install")

        graph = _get_graph()
        meta = _load_stock_meta(symbol)
        trade_date = date.today().isoformat()

        logger.info("propagate %s @ %s ...", symbol, trade_date)
        final_state, decision_raw = graph.propagate(symbol, trade_date)
        logger.info("propagate done: decision_raw=%r", decision_raw)

        return self._to_report(
            symbol=symbol,
            depth=depth,
            meta=meta,
            final_state=final_state,
            decision_raw=decision_raw,
        )

    # ------------------------------------------------------------------
    # mapping helpers (拆出来便于测试)
    # ------------------------------------------------------------------

    def _to_report(
        self,
        symbol: str,
        depth: Literal["deep", "quick"],
        meta: dict,
        final_state: dict,
        decision_raw: str,
    ) -> Report:
        decision, confidence = _normalize_decision(decision_raw)

        bull_history = final_state.get("investment_debate_state", {}).get("bull_history", "")
        bear_history = final_state.get("investment_debate_state", {}).get("bear_history", "")
        market_report = final_state.get("market_report", "") or ""
        fundamentals_report = final_state.get("fundamentals_report", "") or ""
        final_trade_text = final_state.get("final_trade_decision", "") or ""

        price = float(meta.get("current_price") or 0.0)
        # 价格未知时给一个保守区间，不阻塞流水线
        delta = max(price * 0.05, 0.01)
        valid_days = 3 if depth == "deep" else 1

        return Report(
            symbol=symbol,
            name=meta["name"],
            current_price=price,
            summary=_truncate(final_trade_text, 400)
                    or f"{symbol} 综合判断：{decision}",
            technical=TechnicalAnalysis(
                trend=self._extract_trend(market_report),
                key_levels={
                    "support": round(price - delta, 2) if price else 0.0,
                    "resistance": round(price + delta, 2) if price else 0.0,
                },
                summary=_truncate(market_report, 800),
            ),
            fundamental=FundamentalAnalysis(
                industry=meta.get("industry") or "",
                market_cap_yi=meta.get("market_cap_yi"),
                pe_ttm=meta.get("pe_ttm"),
                pb=meta.get("pb"),
                roe=meta.get("roe"),
                summary=_truncate(fundamentals_report, 800),
            ),
            bull_case=_truncate(bull_history, 600) or "（多方观点缺失）",
            bear_case=_truncate(bear_history, 600) or "（空方观点缺失）",
            final_decision=decision,
            confidence=confidence,
            reevaluation_price_range=(
                round(price - delta, 2),
                round(price + delta, 2),
            ),
            valid_until=date.today() + timedelta(days=valid_days),
            risk_warning=(
                "本报告由 TradingAgents 多智能体生成，数据来源 AKShare，"
                "仅供研究参考，不构成投资建议。"
            ),
        )

    @staticmethod
    def _extract_trend(market_report: str) -> str:
        """简单关键词归类。"""
        if not market_report:
            return "未知"
        text = market_report.lower()
        bullish = sum(text.count(k) for k in ("上行", "上涨", "突破", "bullish", "uptrend"))
        bearish = sum(text.count(k) for k in ("下行", "下跌", "破位", "bearish", "downtrend"))
        if bullish > bearish:
            return "上行"
        if bearish > bullish:
            return "下行"
        return "震荡"
