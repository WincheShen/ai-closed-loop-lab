"""MarketBrain — 市场世界模型 + 当日作战计划。

职责：
1. 读取全市场快照 + 当前持仓 + 历史表现，量化指标先行
2. 判断 market regime (bull/neutral/bear/panic/rebound)
3. 用 LLM 综合判断 + 形成 daily plan
4. 输出 MarketRegimeSnapshot，写入 central brain，传给下游 Agent

设计原则：
- 量化指标优先：用规则给出基础 regime，避免 LLM 幻觉决定全局风险
- LLM 只做综合判断、热点解读、操作姿态建议
- 任何下游 Agent 都必须从 MarketBrain 拿到 regime 才能行动
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from src.agents.cio.trading_persona import TradingPersona, get_persona
from src.central_brain import get_central_brain
from src.graph.state import TradingState
from src.infra.logger import get_agent_logger
from src.infra.model_adapter import get_llm
from src.stock_analyzer.data_source import (
    AkshareClient,
    HotSectorDetector,
    MarketSnapshot,
)

logger = get_agent_logger("market_brain", "init")


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

MarketRegime = str  # "bull" | "neutral" | "bear" | "panic" | "rebound"
RiskAppetite = str  # "high" | "medium" | "low"
Posture = str       # "attack" | "selective_attack" | "defend" | "observe" | "exit"


@dataclass
class MarketRegimeSnapshot:
    """市场世界模型输出，贯穿当日所有交易决策。"""

    snapshot_id: str
    trade_date: str
    regime: MarketRegime
    risk_appetite: RiskAppetite
    recommended_posture: Posture
    max_total_position_pct: float
    hot_sectors: list[str] = field(default_factory=list)
    dominant_styles: list[str] = field(default_factory=list)
    avoid_styles: list[str] = field(default_factory=list)
    strategy_bias: dict[str, float] = field(default_factory=dict)
    daily_questions: list[str] = field(default_factory=list)
    summary: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    persona_version: str = ""
    is_mock_data: bool = False
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Market Brain
# ─────────────────────────────────────────────────────────────────────────────

REGIME_SYSTEM_PROMPT = """\
你是一位资深 A 股市场研究主管，负责每日给交易团队下发"作战指令"。
你必须基于给定的量化指标做综合判断，不要凭空臆测。
输出必须是严格 JSON。

## 决策框架
- regime: 必须从以下选一: bull(强势) / neutral(震荡) / bear(弱势) / panic(恐慌) / rebound(反弹)
- risk_appetite: high / medium / low
- recommended_posture: attack(进攻) / selective_attack(精选进攻) / defend(防守) / observe(观望) / exit(撤退)
- dominant_styles: 当前占优的风格 (如 "热点轮动"、"防守蓝筹"、"高股息")
- avoid_styles: 当前应避免的风格
- strategy_bias: 各策略今日权重 (0.0-1.0)，常用策略: hot_sector_pullback / volume_breakout / defensive_bluechip / mean_reversion
- daily_questions: 今日需要持续观察的 2-3 个关键问题
- summary: 一句话总结今日市场判断
"""


REGIME_USER_TEMPLATE = """\
## 市场量化指标 (T 日收盘)
- 涨跌家数: 上涨 {up_count} / 下跌 {down_count} / 平 {flat_count}
- 涨幅 ≥7% 家数: {strong_count}
- 跌幅 ≤-7% 家数: {weak_count}
- 全市场平均涨跌幅: {avg_change_pct:.2f}%
- 量化基础 regime 判定: {base_regime}

## 热点板块 (Top 5)
{hot_sector_block}

## 当前持仓
- 持仓数: {position_count}
- 总市值占用: 不可见 (走 portfolio manager)

## 投资人格约束
{persona_summary}

## 历史表现 (最近)
{performance_block}

请基于以上指标输出今日作战指令 (严格 JSON):
```json
{{
  "regime": "...",
  "risk_appetite": "...",
  "recommended_posture": "...",
  "dominant_styles": ["..."],
  "avoid_styles": ["..."],
  "strategy_bias": {{"hot_sector_pullback": 0.3, "volume_breakout": 0.2}},
  "daily_questions": ["..."],
  "summary": "..."
}}
```
"""


class MarketBrain:
    """市场世界模型 — 每日开盘前生成 regime + daily plan。"""

    def __init__(self, session_id: str, persona: TradingPersona | None = None) -> None:
        self.session_id = session_id
        self.logger = get_agent_logger("market_brain", session_id)
        self.brain = get_central_brain()
        self.persona = persona or get_persona()
        self.akshare = AkshareClient(allow_mock_fallback=True)
        self.hot_detector = HotSectorDetector()

    # ----------------------------------------------------------------------
    # 公共入口
    # ----------------------------------------------------------------------

    def generate_snapshot(self) -> MarketRegimeSnapshot:
        """生成今日 MarketRegimeSnapshot。"""
        self.logger.info("MarketBrain 启动 — 抓取行情并形成 regime")

        snapshot = self.akshare.fetch_snapshot()
        base_regime, evidence = self._compute_base_regime(snapshot)
        hot_results = self.hot_detector.detect(snapshot, top_k=5)
        hot_sectors = [h.sector.name for h in hot_results]

        # LLM 综合判断
        llm_result = self._llm_judge(snapshot, base_regime, hot_results, evidence)

        regime = llm_result.get("regime", base_regime)
        risk_appetite = llm_result.get(
            "risk_appetite", self._default_risk_appetite(regime),
        )
        posture = llm_result.get(
            "recommended_posture", self._default_posture(regime),
        )

        max_pos = self.persona.max_total_position_for(regime)

        result = MarketRegimeSnapshot(
            snapshot_id=f"REG-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}",
            trade_date=str(snapshot.snapshot_date),
            regime=regime,
            risk_appetite=risk_appetite,
            recommended_posture=posture,
            max_total_position_pct=max_pos,
            hot_sectors=hot_sectors,
            dominant_styles=list(llm_result.get("dominant_styles", [])),
            avoid_styles=list(llm_result.get("avoid_styles", [])),
            strategy_bias=dict(llm_result.get("strategy_bias", {})),
            daily_questions=list(llm_result.get("daily_questions", [])),
            summary=llm_result.get("summary", ""),
            evidence=evidence,
            persona_version=self.persona.persona_version,
            is_mock_data=snapshot.is_mock,
            created_at=datetime.now().isoformat(),
        )

        self.brain.store.save_market_regime(self.session_id, result.to_dict())
        self.brain.log_agent_event(
            self.session_id, "market_brain", "regime_generated",
            {
                "regime": result.regime,
                "posture": result.recommended_posture,
                "max_position_pct": result.max_total_position_pct,
                "hot_sectors": result.hot_sectors,
                "is_mock": result.is_mock_data,
            },
        )

        self.logger.info(
            "Market regime: %s | posture=%s | max_pos=%.0f%% | hot=%s",
            result.regime, result.recommended_posture,
            result.max_total_position_pct * 100,
            ", ".join(result.hot_sectors[:3]) or "无",
        )
        self.logger.info("Summary: %s", result.summary)
        return result

    # ----------------------------------------------------------------------
    # 量化基础 regime
    # ----------------------------------------------------------------------

    def _compute_base_regime(
        self, snapshot: MarketSnapshot,
    ) -> tuple[MarketRegime, dict[str, Any]]:
        """基于涨跌家数 + 极值家数的量化基础判断（先于 LLM）。"""
        stocks = snapshot.stocks or []
        up = sum(1 for s in stocks if s.change_pct > 0)
        down = sum(1 for s in stocks if s.change_pct < 0)
        flat = len(stocks) - up - down
        strong = sum(1 for s in stocks if s.change_pct >= 7.0)
        weak = sum(1 for s in stocks if s.change_pct <= -7.0)
        avg_change = sum(s.change_pct for s in stocks) / len(stocks) if stocks else 0.0

        # 简单 regime 量化规则
        up_ratio = up / max(len(stocks), 1)
        if avg_change >= 1.5 and strong >= 50 and weak < 30:
            regime = "bull"
        elif avg_change <= -1.5 and weak >= 80:
            regime = "panic" if weak >= 200 else "bear"
        elif avg_change >= 0.8 and up_ratio >= 0.55:
            regime = "rebound"
        elif avg_change <= -0.8 or up_ratio <= 0.35:
            regime = "bear"
        else:
            regime = "neutral"

        evidence = {
            "up_count": up,
            "down_count": down,
            "flat_count": flat,
            "strong_count": strong,
            "weak_count": weak,
            "avg_change_pct": round(avg_change, 2),
            "up_ratio": round(up_ratio, 3),
            "total_stocks": len(stocks),
            "is_mock": snapshot.is_mock,
        }
        return regime, evidence

    # ----------------------------------------------------------------------
    # LLM 综合判断
    # ----------------------------------------------------------------------

    def _llm_judge(
        self,
        snapshot: MarketSnapshot,
        base_regime: MarketRegime,
        hot_results: list,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """调用 LLM 综合判断 regime + daily plan。失败时返回基于规则的默认值。"""
        hot_block = "\n".join(
            f"- {h.sector.name}: {h.sector.change_pct:+.2f}% (rank {h.rank})"
            for h in hot_results
        ) or "无明显热点板块"

        recent_perf = self._recent_performance_block()

        user_msg = REGIME_USER_TEMPLATE.format(
            up_count=evidence["up_count"],
            down_count=evidence["down_count"],
            flat_count=evidence["flat_count"],
            strong_count=evidence["strong_count"],
            weak_count=evidence["weak_count"],
            avg_change_pct=evidence["avg_change_pct"],
            base_regime=base_regime,
            hot_sector_block=hot_block,
            position_count=len(self.brain.store.list_open_positions()),
            persona_summary=self.persona.prompt_summary(),
            performance_block=recent_perf,
        )

        try:
            llm = get_llm()
            response = llm.invoke([
                {"role": "system", "content": REGIME_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ])
            return self._parse_json(response.content)
        except Exception as e:  # noqa: BLE001
            self.logger.warning("LLM 判断失败 (%s)，使用规则默认值", e)
            return self._fallback_judgment(base_regime)

    def _parse_json(self, content: str) -> dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            # 去除 ```json ... ```
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
            if text.endswith("```"):
                text = text[: -3].strip()
        try:
            return json.loads(text)
        except Exception:
            # 尝试找第一个 { 到最后一个 }
            l = text.find("{")
            r = text.rfind("}")
            if l >= 0 and r > l:
                return json.loads(text[l : r + 1])
            raise

    def _fallback_judgment(self, regime: MarketRegime) -> dict[str, Any]:
        return {
            "regime": regime,
            "risk_appetite": self._default_risk_appetite(regime),
            "recommended_posture": self._default_posture(regime),
            "dominant_styles": [],
            "avoid_styles": [],
            "strategy_bias": {},
            "daily_questions": [],
            "summary": f"LLM 不可用，按量化规则判定为 {regime}",
        }

    @staticmethod
    def _default_risk_appetite(regime: MarketRegime) -> RiskAppetite:
        return {
            "bull": "high", "rebound": "medium", "neutral": "medium",
            "bear": "low", "panic": "low",
        }.get(regime, "medium")

    @staticmethod
    def _default_posture(regime: MarketRegime) -> Posture:
        return {
            "bull": "attack",
            "rebound": "selective_attack",
            "neutral": "selective_attack",
            "bear": "defend",
            "panic": "exit",
        }.get(regime, "selective_attack")

    def _recent_performance_block(self) -> str:
        """最近表现摘要（持仓数 + 简单汇总），Phase 1 简化版。"""
        positions = self.brain.store.list_open_positions()
        if not positions:
            return "暂无持仓"
        return (
            f"持仓 {len(positions)} 只: "
            + ", ".join(
                f"{p.get('symbol')}({p.get('original_strategy') or 'n/a'})"
                for p in positions[:5]
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# LangGraph 节点
# ─────────────────────────────────────────────────────────────────────────────

def run_market_brain_node(state: TradingState) -> dict[str, Any]:
    """LangGraph 节点 — 每日开盘前判断 market regime。

    输入：TradingState (session_id 即可)
    输出：market_regime 字段 + logs
    """
    session_id = state["session_id"]
    persona = get_persona()
    brain = MarketBrain(session_id, persona=persona)
    snapshot = brain.generate_snapshot()

    return {
        "market_regime": snapshot.to_dict(),
        "persona_version": persona.persona_version,
        "hot_sectors": snapshot.hot_sectors,  # 兼容下游 Explorer
        "timestamp": datetime.now().isoformat(),
        "logs": state.get("logs", []) + [
            f"[MarketBrain] regime={snapshot.regime} posture={snapshot.recommended_posture} "
            f"max_pos={snapshot.max_total_position_pct:.0%} hot={','.join(snapshot.hot_sectors[:3])}"
        ],
    }
