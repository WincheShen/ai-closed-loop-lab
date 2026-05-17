"""Strategist Engine — 决策者核心实现。

职责：
1. 对 Explorer 选出的候选票用 LLM 进行深度分析（技术/资金/热点）
2. 结合交易规则约束和投资人格 (TradingPersona) 生成带真实价格的 TradeSignal
3. 只为 LLM 判定 BUY 的标的生成信号，PASS 的不出信号
4. Phase 1: 接收 MarketBrain 的 regime + posture + strategy_bias 作为决策上下文
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from src.agents.cio.trading_persona import TradingPersona, get_persona
from src.central_brain import get_central_brain
from src.graph.state import StockCandidate, TradeSignal, TradingState
from src.infra.config import cfg
from src.infra.logger import get_agent_logger
from src.infra.model_adapter import get_llm

logger = get_agent_logger("strategist", "init")

MAX_LLM_ANALYSIS = 8

# 策略人类名称 → strategy_id 映射（用于 RiskGovernor 的 persona 兼容性检查）
_STRATEGY_NAME_TO_ID = {
    "20日线回踩": "hot_sector_pullback",
    "热点板块前排回踩": "hot_sector_pullback",
    "热点回踩": "hot_sector_pullback",
    "回踩": "hot_sector_pullback",
    "放量突破": "volume_breakout",
    "突破前高": "volume_breakout",
    "MACD金叉": "volume_breakout",
    "防守蓝筹": "defensive_bluechip",
    "高股息": "defensive_bluechip",
    "均值回归": "mean_reversion",
    "超跌反弹": "mean_reversion",
}


def _infer_strategy_id(strategy_name: str) -> str:
    """从人类可读策略名称推断标准 strategy_id。"""
    if not strategy_name:
        return "unknown"
    name = strategy_name.strip()
    if name in _STRATEGY_NAME_TO_ID:
        return _STRATEGY_NAME_TO_ID[name]
    # 模糊匹配
    for key, sid in _STRATEGY_NAME_TO_ID.items():
        if key in name:
            return sid
    return "unknown"

# ─────────────────────────────────────────────────────────────────
# LLM Prompts
# ─────────────────────────────────────────────────────────────────

STRATEGIST_SYSTEM_PROMPT = """\
你是一位 Cognitive Agent 的交易决策者。你必须严格遵守"投资人格"约束，
并根据"当日市场作战指令"调整决策。不要每只票都给 BUY，宁可错过不要做错。

## 决策原则
1. 必须先读懂今日 market_regime 和 recommended_posture，再判断个股
2. 在禁用策略 (forbidden) 上不要 BUY
3. 在 degraded 策略上要明显降低仓位
4. 风险收益比 < 1.2 一律 PASS
5. 弱势市场 (bear/panic) 必须更严格，能不交易就不交易

## 决策框架
- BUY: 技术形态良好 + 资金面支持 + 与当日 posture 匹配 + 风险收益比合理
- PASS: 任一关键条件不满足，或与 persona 禁忌冲突

## 输出格式（严格 JSON）
```json
{
    "action": "BUY 或 PASS",
    "entry_price": 建议入场价,
    "target_price": 目标价,
    "stop_loss": 止损价,
    "position_pct": 建议仓位比例(0.02-0.10),
    "strategy": "策略名称(如 热点板块前排回踩/放量突破/防守蓝筹/均值回归 等)",
    "confidence": 0.0到1.0,
    "rationale": "完整的买入或不买逻辑(2-3句话, 必须引用 regime/posture)",
    "bull_case": "最大的看多理由",
    "bear_case": "最大的风险点"
}
```
"""

STRATEGIST_USER_TEMPLATE = """\
{persona_block}

## 今日市场作战指令 (来自 MarketBrain)
- regime: {regime}  | posture: {posture}  | risk_appetite: {risk_appetite}
- 总仓位上限: {max_total_pos:.0%}
- 偏好策略 (bias): {strategy_bias}
- 应避免风格: {avoid_styles}
- 摘要: {regime_summary}

## 候选股票
- 代码: {symbol}
- 名称: {name}
- 所属板块: {sector}

## 入选理由
{hot_reason}

## 行情数据
- 当前价格: {price}
- 今日涨跌: {change_pct}%
- PE(TTM): {pe_ttm}
- PB: {pb}
- 市值: {market_cap}亿

## 技术指标
- MA5: {ma5} | MA10: {ma10} | MA20: {ma20}
- 价格 vs MA5: {price_vs_ma5} | vs MA20: {price_vs_ma20}
- 近5日涨幅: {recent_5d}%
- 近10日高点: {high_10d} | 低点: {low_10d}
- 量比(今日/20日均量): {vol_ratio}
- 趋势: {trend}

## 资金面
- 主力净流入: {inflow_wan}万
- 成交额: {turnover_yi}亿
- 换手率: {turnover_rate}%

## 当前热点板块
{hot_sectors}

请基于以上信息，特别是结合 regime 和 persona 给出决策。
"""


class StrategistEngine:
    """决策者引擎 — LLM 驱动的候选股分析。"""

    def __init__(
        self,
        session_id: str,
        hot_sectors: list[str] | None = None,
        persona: TradingPersona | None = None,
        market_regime: dict[str, Any] | None = None,
    ) -> None:
        self.session_id = session_id
        self.logger = get_agent_logger("strategist", session_id)
        self.brain = get_central_brain()
        self.config = cfg()
        self.hot_sectors = hot_sectors or []
        self.persona = persona or get_persona()
        self.market_regime = market_regime or {}

    def _persona_block(self) -> str:
        return self.persona.prompt_summary()

    def _regime_kwargs(self) -> dict[str, Any]:
        regime = self.market_regime or {}
        return {
            "regime": regime.get("regime", "neutral"),
            "posture": regime.get("recommended_posture", "selective_attack"),
            "risk_appetite": regime.get("risk_appetite", "medium"),
            "max_total_pos": float(
                regime.get(
                    "max_total_position_pct",
                    self.persona.max_total_position_for(regime.get("regime", "neutral")),
                )
            ),
            "strategy_bias": ", ".join(
                f"{k}={v:.2f}" for k, v in (regime.get("strategy_bias") or {}).items()
            ) or "无明显偏好",
            "avoid_styles": ", ".join(regime.get("avoid_styles", [])) or "无",
            "regime_summary": regime.get("summary", "—"),
        }

    def analyze_candidate(self, candidate: StockCandidate) -> TradeSignal | None:
        """对单只候选票调用 LLM 深度分析，返回 TradeSignal 或 None（PASS）。"""
        symbol = candidate["symbol"]
        name = candidate["name"]
        kline = candidate.get("kline_summary", {})
        fund = candidate.get("fund_flow", {})

        user_msg = STRATEGIST_USER_TEMPLATE.format(
            persona_block=self._persona_block(),
            symbol=symbol,
            name=name,
            sector=candidate.get("sector", "未知"),
            hot_reason="\n".join(
                f"- {r}" for r in candidate.get("hot_reason", [])
            ),
            price=kline.get("current_price", 0),
            change_pct=kline.get("change_pct", 0),
            pe_ttm=kline.get("pe_ttm") or "N/A",
            pb=kline.get("pb") or "N/A",
            market_cap=kline.get("market_cap_yi") or "N/A",
            ma5=kline.get("ma5", "N/A"),
            ma10=kline.get("ma10", "N/A"),
            ma20=kline.get("ma20", "N/A"),
            price_vs_ma5=kline.get("price_vs_ma5", "N/A"),
            price_vs_ma20=kline.get("price_vs_ma20", "N/A"),
            recent_5d=kline.get("recent_5d_change_pct", "N/A"),
            high_10d=kline.get("recent_high_10d", "N/A"),
            low_10d=kline.get("recent_low_10d", "N/A"),
            vol_ratio=kline.get("vol_ratio", "N/A"),
            trend=kline.get("trend", "N/A"),
            inflow_wan=round(fund.get("main_net_inflow", 0) / 1e4, 1),
            turnover_yi=round(fund.get("turnover", 0) / 1e8, 2),
            turnover_rate=fund.get("turnover_rate", 0),
            hot_sectors=", ".join(self.hot_sectors) if self.hot_sectors else "无",
            **self._regime_kwargs(),
        )

        try:
            llm = get_llm()
            messages = [
                {"role": "system", "content": STRATEGIST_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ]
            response = llm.invoke(messages)
            result = self._parse_response(response.content)
        except Exception as e:
            self.logger.error("LLM 分析失败 %s: %s", symbol, e)
            return None

        if result.get("action") != "BUY":
            self.logger.info(
                "[%s %s] PASS — %s", symbol, name, result.get("rationale", "")[:80],
            )
            return None

        current_price = kline.get("current_price", 0)
        entry_price = result.get("entry_price") or current_price
        if entry_price <= 0:
            entry_price = current_price
        stop_loss_pct = self.config.get("default_stop_loss_pct", 0.05)

        signal: TradeSignal = {
            "signal_id": f"SIG-{uuid.uuid4().hex[:8].upper()}",
            "symbol": symbol,
            "action": "buy",
            "entry_price": round(entry_price, 2),
            "target_price": round(
                result.get("target_price") or entry_price * 1.08, 2,
            ),
            "stop_loss": round(
                result.get("stop_loss") or entry_price * (1 - stop_loss_pct), 2,
            ),
            "position_pct": min(
                result.get("position_pct", 0.08),
                self.config.get("max_position_pct", 0.10),
            ),
            "strategy": result.get("strategy", "LLM综合分析"),
            "rationale": result.get("rationale", ""),
            "timestamp": datetime.now().isoformat(),
            "expiry": (datetime.now() + timedelta(days=5)).isoformat(),
        }

        # Attach extra fields for downstream RiskGovernor / Position creation
        signal["name"] = name  # type: ignore[typeddict-unknown-key]
        signal["sector"] = candidate.get("sector", "")  # type: ignore[typeddict-unknown-key]
        signal["bull_case"] = result.get("bull_case", "")  # type: ignore[typeddict-unknown-key]
        signal["bear_case"] = result.get("bear_case", "")  # type: ignore[typeddict-unknown-key]
        signal["confidence"] = result.get("confidence", 0.5)  # type: ignore[typeddict-unknown-key]
        signal["strategy_id"] = _infer_strategy_id(signal["strategy"])  # type: ignore[typeddict-unknown-key]
        # 来自 MarketBrain 的认知元数据
        signal["market_regime"] = self.market_regime.get("regime", "")  # type: ignore[typeddict-unknown-key]
        signal["persona_version"] = self.persona.persona_version  # type: ignore[typeddict-unknown-key]

        self.logger.info(
            "生成信号 %s | %s %s | 策略=%s (id=%s) | regime=%s | 入场=%.2f | 止损=%.2f | 目标=%.2f | 置信=%.0f%%",
            signal["signal_id"], symbol, name, signal["strategy"],
            signal["strategy_id"], signal["market_regime"],  # type: ignore[typeddict-unknown-key]
            signal["entry_price"], signal["stop_loss"], signal["target_price"],
            result.get("confidence", 0) * 100,
        )
        return signal

    def generate_signals(
        self, candidates: list[StockCandidate],
    ) -> list[TradeSignal]:
        """批量分析候选票（上限 MAX_LLM_ANALYSIS 只），返回 BUY 信号。"""
        signals: list[TradeSignal] = []
        analyze_count = min(len(candidates), MAX_LLM_ANALYSIS)
        self.logger.info("开始 LLM 深度分析 — Top %d 候选", analyze_count)

        for c in candidates[:analyze_count]:
            sig = self.analyze_candidate(c)
            if sig:
                signals.append(sig)
                self.brain.store.save_trade_signal(self.session_id, sig)

        self.brain.log_agent_event(
            self.session_id, "strategist", "signals_generated",
            {
                "analyzed": analyze_count,
                "buy_count": len(signals),
                "symbols": [s["symbol"] for s in signals],
            },
        )
        self.logger.info(
            "LLM 分析完成 — 分析 %d 只, 买入信号 %d 条", analyze_count, len(signals),
        )
        return signals

    def risk_assessment(self, signals: list[TradeSignal]) -> dict:
        """整体风控评估：集中度、总仓位。"""
        total_position = sum(s["position_pct"] for s in signals)
        assessment = {
            "total_position_pct": round(total_position, 2),
            "signal_count": len(signals),
            "max_single_position": max(
                (s["position_pct"] for s in signals), default=0,
            ),
            "risk_level": (
                "high" if total_position > 0.5
                else "medium" if total_position > 0.3
                else "low"
            ),
            "warnings": [],
        }
        if total_position > 0.5:
            assessment["warnings"].append("总仓位超过50%，建议减仓")
        if len(signals) > 5:
            assessment["warnings"].append("持仓标的过多，建议精选")
        return assessment

    def _parse_response(self, content: str) -> dict:
        """解析 LLM JSON 输出，容错处理。"""
        text = content.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            self.logger.warning("LLM 输出不是有效 JSON，尝试关键字提取")
            action = "PASS"
            for a in ("BUY", "PASS"):
                if a in content.upper():
                    action = a
                    break
            return {
                "action": action,
                "rationale": content[:200],
                "confidence": 0.3,
            }


def run_strategy_node(state: TradingState) -> dict[str, Any]:
    """LangGraph 节点函数 — 决策者分析。

    输入：含 target_stocks + market_regime 的 TradingState
    输出：{"trade_signals": [...], "risk_assessment": {...}}
    """
    session_id = state["session_id"]
    hot_sectors = state.get("hot_sectors", [])
    market_regime = state.get("market_regime") or {}
    persona = get_persona()
    engine = StrategistEngine(
        session_id,
        hot_sectors=hot_sectors,
        persona=persona,
        market_regime=market_regime,
    )

    candidates = state.get("target_stocks", [])
    if not candidates:
        return {
            "trade_signals": [],
            "risk_assessment": {"error": "无候选票输入"},
            "logs": state.get("logs", []) + ["[Strategist] 无候选票，跳过"],
        }

    signals = engine.generate_signals(candidates)
    risk = engine.risk_assessment(signals)

    for sig in signals:
        get_central_brain().bus.emit_trade_signal(sig)

    return {
        "trade_signals": signals,
        "risk_assessment": risk,
        "logs": state.get("logs", []) + [
            f"[Strategist] regime={market_regime.get('regime', 'n/a')} 分析 "
            f"{min(len(candidates), MAX_LLM_ANALYSIS)} 只, "
            f"生成 {len(signals)} 条买入信号, 风控={risk['risk_level']}"
        ],
    }
