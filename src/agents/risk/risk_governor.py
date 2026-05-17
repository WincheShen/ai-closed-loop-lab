"""RiskGovernor — 独立风控官。

设计原则:
- 完全独立于 Strategist，Strategist 提出买卖、RiskGovernor 拥有 veto 权
- 检查项: 总仓位、单票仓位、板块集中度、策略集中度、连续亏损、market regime 兼容
- 输出 RiskDecision (approve/reduce/reject) + reason + risk_flags
- 所有 BUY 信号必须经过 RiskGovernor 才能进入 Executioner

Phase 1 实现:
- 基于 TradingPersona + MarketRegimeSnapshot + 当前持仓做规则化判断
- 不调用 LLM (规则可解释、可回测、可复盘)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from src.agents.cio.trading_persona import TradingPersona, get_persona
from src.central_brain import get_central_brain
from src.graph.state import TradeSignal, TradingState
from src.infra.config import cfg
from src.infra.logger import get_agent_logger

logger = get_agent_logger("risk_governor", "init")


RiskDecisionType = str  # "approve" | "reduce" | "reject"


@dataclass
class RiskDecision:
    """单条信号的风控裁决结果。"""

    signal_id: str
    symbol: str
    decision: RiskDecisionType
    original_position_pct: float
    approved_position_pct: float
    reason: str
    risk_flags: list[str] = field(default_factory=list)
    market_regime: str = ""
    persona_version: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RiskGovernor:
    """独立风控官 — 对每条 BUY 信号做 approve/reduce/reject 裁决。"""

    def __init__(
        self,
        session_id: str,
        persona: TradingPersona | None = None,
        market_regime: dict[str, Any] | None = None,
    ) -> None:
        self.session_id = session_id
        self.logger = get_agent_logger("risk_governor", session_id)
        self.brain = get_central_brain()
        self.persona = persona or get_persona()
        self.market_regime = market_regime or {}
        self.regime: str = self.market_regime.get("regime", "neutral")
        self.posture: str = self.market_regime.get("recommended_posture", "selective_attack")
        self.max_total_position_pct: float = float(
            self.market_regime.get(
                "max_total_position_pct",
                self.persona.max_total_position_for(self.regime),
            )
        )
        # 缓存当前持仓状态
        self._positions = self.brain.store.list_open_positions()
        self._capital = float(cfg().get("initial_capital", 300000))

    # ----------------------------------------------------------------------
    # 公共入口
    # ----------------------------------------------------------------------

    def evaluate_signals(
        self, signals: list[TradeSignal],
    ) -> tuple[list[TradeSignal], list[RiskDecision]]:
        """对一批信号做风控，返回 (approved_signals, decisions)。

        - approved/reduce 的信号会进入 approved_signals (position_pct 可能被减半)
        - reject 的信号不会进入 approved_signals
        - decisions 包含每条信号的完整决策记录
        """
        decisions: list[RiskDecision] = []
        approved: list[TradeSignal] = []

        # posture=exit 时拒绝所有新买
        if self.posture == "exit":
            for sig in signals:
                d = self._reject(sig, "市场处于撤退状态，禁止新开仓", ["posture_exit"])
                decisions.append(d)
            self._persist_decisions(decisions)
            self._summary_log(signals, decisions)
            return [], decisions

        # 当前已用仓位（按 持仓市值/初始资金 估算）
        used_pct = self._current_used_pct()
        self.logger.info(
            "RiskGovernor 启动 — regime=%s posture=%s 已用仓位=%.0f%% 上限=%.0f%%",
            self.regime, self.posture, used_pct * 100,
            self.max_total_position_pct * 100,
        )

        sector_pct = self._sector_exposure()

        for sig in signals:
            decision = self._evaluate_one(sig, used_pct, sector_pct)
            decisions.append(decision)

            if decision.decision == "reject":
                continue

            # approved / reduce → 修改 position_pct 并放行
            approved_sig: TradeSignal = dict(sig)  # type: ignore[assignment]
            approved_sig["position_pct"] = decision.approved_position_pct
            # 在 signal 上挂上 risk 元信息（便于下游记录）
            approved_sig["risk_decision"] = decision.decision  # type: ignore[typeddict-unknown-key]
            approved_sig["risk_reason"] = decision.reason  # type: ignore[typeddict-unknown-key]
            approved_sig["market_regime"] = self.regime  # type: ignore[typeddict-unknown-key]
            approved_sig["persona_version"] = self.persona.persona_version  # type: ignore[typeddict-unknown-key]
            approved.append(approved_sig)

            # 更新已用仓位（防止后续信号超出）
            used_pct += decision.approved_position_pct

        self._persist_decisions(decisions)
        self._summary_log(signals, decisions)
        return approved, decisions

    # ----------------------------------------------------------------------
    # 核心规则
    # ----------------------------------------------------------------------

    def _evaluate_one(
        self,
        signal: TradeSignal,
        used_pct: float,
        sector_pct: dict[str, float],
    ) -> RiskDecision:
        flags: list[str] = []
        original_pct = float(signal.get("position_pct", 0.08))
        approved_pct = original_pct

        # 1. 策略-regime 兼容性 (优先用 strategy_id, 没有则降级 strategy 名称)
        strategy_id = (
            signal.get("strategy_id")  # type: ignore[typeddict-item]
            or signal.get("strategy", "")
            or "unknown"
        )
        compat = self.persona.is_strategy_allowed(strategy_id, self.regime)
        if compat == "forbidden":
            return self._reject(
                signal, f"策略 {strategy_id} 在 {self.regime} 市场下被禁用",
                ["strategy_regime_forbidden"],
            )
        if compat == "degraded":
            flags.append("strategy_regime_degraded")
            approved_pct *= 0.5

        # 2. 单票仓位上限
        single_cap = self.persona.max_single_position_pct
        if approved_pct > single_cap:
            flags.append("single_position_capped")
            approved_pct = single_cap

        # 3. 总仓位上限
        if used_pct + approved_pct > self.max_total_position_pct:
            remaining = self.max_total_position_pct - used_pct
            if remaining <= 0.01:
                return self._reject(
                    signal, f"总仓位已达 {self.max_total_position_pct:.0%} 上限",
                    ["total_position_exceeded"],
                )
            flags.append("total_position_reduced")
            approved_pct = max(0.02, remaining)  # 至少 2% 否则没意义

        # 4. 板块集中度（板块信息可能为空）
        sector = self._signal_sector(signal)
        if sector:
            current_sector_pct = sector_pct.get(sector, 0.0)
            sector_cap = self.persona.max_sector_concentration_pct
            if current_sector_pct + approved_pct > sector_cap:
                remaining = sector_cap - current_sector_pct
                if remaining <= 0.01:
                    return self._reject(
                        signal, f"板块 {sector} 已达集中度上限 {sector_cap:.0%}",
                        ["sector_concentration"],
                    )
                flags.append("sector_concentration_reduced")
                approved_pct = min(approved_pct, max(0.02, remaining))

        # 5. 重复持仓
        if any(p["symbol"] == signal["symbol"] for p in self._positions):
            return self._reject(
                signal, f"{signal['symbol']} 已有持仓，不重复建仓",
                ["duplicate_position"],
            )

        # 6. 风险收益比
        entry = float(signal.get("entry_price", 0) or 0)
        target = float(signal.get("target_price", 0) or 0)
        stop = float(signal.get("stop_loss", 0) or 0)
        if entry > 0 and stop > 0 and target > entry > stop:
            upside = target - entry
            downside = entry - stop
            rr = upside / downside if downside > 0 else 0
            if rr < 1.2:
                return self._reject(
                    signal, f"风险收益比 {rr:.2f} 过低 (<1.2)",
                    ["bad_risk_reward"],
                )

        # 7. 弱势市场进一步降仓
        if self.regime in ("bear", "panic"):
            flags.append("weak_regime_haircut")
            approved_pct *= 0.5

        # 最终裁决
        approved_pct = round(max(0.02, min(approved_pct, single_cap)), 4)
        decision_type: RiskDecisionType = (
            "approve" if approved_pct >= original_pct - 1e-6 else "reduce"
        )
        reason = "通过风控" if decision_type == "approve" else (
            f"仓位由 {original_pct:.0%} 调整为 {approved_pct:.0%}: {','.join(flags)}"
        )
        return RiskDecision(
            signal_id=signal["signal_id"],
            symbol=signal["symbol"],
            decision=decision_type,
            original_position_pct=original_pct,
            approved_position_pct=approved_pct,
            reason=reason,
            risk_flags=flags,
            market_regime=self.regime,
            persona_version=self.persona.persona_version,
            created_at=datetime.now().isoformat(),
        )

    # ----------------------------------------------------------------------
    # 辅助
    # ----------------------------------------------------------------------

    def _reject(
        self, signal: TradeSignal, reason: str, flags: list[str],
    ) -> RiskDecision:
        original = float(signal.get("position_pct", 0.08))
        return RiskDecision(
            signal_id=signal["signal_id"],
            symbol=signal["symbol"],
            decision="reject",
            original_position_pct=original,
            approved_position_pct=0.0,
            reason=reason,
            risk_flags=flags,
            market_regime=self.regime,
            persona_version=self.persona.persona_version,
            created_at=datetime.now().isoformat(),
        )

    def _current_used_pct(self) -> float:
        """当前持仓占用的资金比例（粗略）。"""
        total_cost = sum(
            float(p.get("entry_price", 0)) * int(p.get("current_qty", 0))
            for p in self._positions
        )
        return total_cost / self._capital if self._capital > 0 else 0.0

    def _sector_exposure(self) -> dict[str, float]:
        """计算各板块当前仓位占比（持仓没有 sector 字段时返回空）。"""
        out: dict[str, float] = {}
        for p in self._positions:
            sec = p.get("sector") or ""
            if not sec:
                continue
            cost = float(p.get("entry_price", 0)) * int(p.get("current_qty", 0))
            out[sec] = out.get(sec, 0.0) + cost / self._capital
        return out

    def _signal_sector(self, signal: TradeSignal) -> str:
        """从 signal 上的扩展字段提取板块（Strategist 应该挂上）。"""
        return signal.get("sector", "") or ""  # type: ignore[typeddict-item]

    def _persist_decisions(self, decisions: list[RiskDecision]) -> None:
        for d in decisions:
            self.brain.store.save_risk_decision(self.session_id, d.to_dict())

    def _summary_log(
        self, signals: list[TradeSignal], decisions: list[RiskDecision],
    ) -> None:
        approve = sum(1 for d in decisions if d.decision == "approve")
        reduce = sum(1 for d in decisions if d.decision == "reduce")
        reject = sum(1 for d in decisions if d.decision == "reject")
        self.logger.info(
            "RiskGovernor 完成 — 输入 %d 条 → approve %d / reduce %d / reject %d",
            len(signals), approve, reduce, reject,
        )
        for d in decisions:
            tag = {"approve": "✓", "reduce": "△", "reject": "✗"}.get(d.decision, "?")
            self.logger.info(
                "  %s %s %s | %.0f%% → %.0f%% | %s",
                tag, d.signal_id, d.symbol,
                d.original_position_pct * 100, d.approved_position_pct * 100,
                d.reason,
            )


# ─────────────────────────────────────────────────────────────────────────────
# LangGraph 节点
# ─────────────────────────────────────────────────────────────────────────────

def run_risk_governor_node(state: TradingState) -> dict[str, Any]:
    """LangGraph 节点 — 对 Strategist 的信号做风控。

    输入: trade_signals + market_regime + persona_version
    输出: trade_signals (经过过滤的) + risk_decisions
    """
    session_id = state["session_id"]
    signals = state.get("trade_signals", [])
    market_regime = state.get("market_regime") or {}
    persona = get_persona()

    if not signals:
        return {
            "trade_signals": [],
            "risk_decisions": [],
            "logs": state.get("logs", []) + [
                "[RiskGovernor] 无信号，跳过风控"
            ],
        }

    governor = RiskGovernor(
        session_id, persona=persona, market_regime=market_regime,
    )
    approved, decisions = governor.evaluate_signals(signals)

    return {
        "trade_signals": approved,
        "risk_decisions": [d.to_dict() for d in decisions],
        "logs": state.get("logs", []) + [
            f"[RiskGovernor] 输入 {len(signals)} → 通过 {len(approved)} "
            f"({sum(1 for d in decisions if d.decision == 'reject')} reject)"
        ],
    }
