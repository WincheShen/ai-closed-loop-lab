"""交易归因引擎 — 每笔平仓自动生成结构化归因 + lesson。

职责:
1. 对已关闭仓位比较 entry vs close 时的 regime/thesis/价格
2. 规则先行判定 primary_cause（止损/止盈/regime 变化/持仓超时等）
3. LLM 生成 lesson（一句话教训）和 should_have（如果重来应该怎么做）
4. 写入 trade_attributions + lessons 表，供 Strategist 决策时检索

触发方式:
    在 intraday_loop.py 的 close_position 调用后紧跟 attribute()。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from src.central_brain import get_central_brain
from src.infra.logger import get_agent_logger
from src.infra.model_adapter import get_llm

logger = get_agent_logger("attribution", "init")


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

# 主因分类 — 规则判定，不走 LLM
PRIMARY_CAUSES = [
    "thesis_correct",          # 逻辑正确，按计划盈利
    "thesis_wrong",            # 选股逻辑错误 (买入后走势与预期相反)
    "timing_early",            # 时机太早 (先跌再涨，止损出局)
    "timing_late",             # 追高 (entry 已在近期高位)
    "regime_shift",            # 持仓期间 regime 突变
    "stop_loss_triggered",     # 正常止损
    "take_profit_triggered",   # 正常止盈
    "position_too_large",      # 仓位过重放大亏损
    "held_too_long",           # 超出预期持仓天数
    "external_shock",          # 无法预见的外部冲击
]


@dataclass
class TradeAttribution:
    """单笔交易归因记录。"""

    attribution_id: str
    position_id: str
    symbol: str
    name: str

    # 结果
    entry_price: float
    close_price: float
    realized_pnl: float
    pnl_pct: float
    holding_days: int

    # 归因分解 (规则判定)
    outcome: str                        # win / loss / breakeven
    primary_cause: str                  # PRIMARY_CAUSES 之一
    secondary_causes: list[str] = field(default_factory=list)

    # 上下文对比
    entry_regime: str = ""
    close_regime: str = ""
    regime_changed: bool = False
    strategy_id: str = ""
    original_thesis: str = ""
    actual_narrative: str = ""          # LLM 生成

    # LLM 生成
    lesson: str = ""                    # 一句话教训
    should_have: str = ""               # "如果重来，应该..."
    tags: list[str] = field(default_factory=list)

    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Lesson:
    """可检索的教训。从 TradeAttribution 提取后独立存储。"""

    lesson_id: str
    attribution_id: str
    symbol: str
    strategy_id: str
    regime: str
    outcome: str                        # win / loss
    lesson_text: str
    tags: list[str] = field(default_factory=list)
    relevance_score: float = 1.0
    cited_count: int = 0
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# 归因引擎
# ─────────────────────────────────────────────────────────────────────────────

ATTRIBUTION_SYSTEM_PROMPT = """\
你是一位严格的交易复盘专家。给你一笔已关闭交易的完整信息，请生成：
1. actual_narrative: 这笔交易实际发生了什么 (2-3 句话)
2. lesson: 一句话总结教训 (不超过 40 字)
3. should_have: "如果重来，应该..." (一句话)
4. tags: 2-4 个可检索标签 (如 "追高", "止损正确", "regime变化", "板块轮动")

输出严格 JSON:
```json
{
  "actual_narrative": "...",
  "lesson": "...",
  "should_have": "...",
  "tags": ["...", "..."]
}
```
"""

ATTRIBUTION_USER_TEMPLATE = """\
## 交易信息
- 标的: {symbol} {name}
- 策略: {strategy} (strategy_id: {strategy_id})
- 结果: {outcome} | PnL: {pnl_pct:+.2f}%
- 持仓天数: {holding_days}

## 买入时
- 价格: {entry_price:.2f}
- Regime: {entry_regime}
- 买入逻辑: {original_thesis}
- Bull case: {bull_case}
- Bear case: {bear_case}

## 卖出时
- 价格: {close_price:.2f}
- Regime: {close_regime}
- Regime 是否变化: {regime_changed}

## 规则判定的主因
- primary_cause: {primary_cause}

请基于以上信息生成归因。
"""


class TradeAttributor:
    """归因引擎 — 对已关闭仓位生成结构化归因 + lesson。"""

    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id
        self.logger = get_agent_logger("attribution", session_id or "default")
        self.brain = get_central_brain()

    def attribute(self, position: dict, close_price: float | None = None) -> TradeAttribution:
        """对已关闭仓位生成归因。

        Args:
            position: positions 表的一行 (dict)，需要包含:
                position_id, symbol, name, entry_price, close_price,
                realized_pnl, entry_date, closed_at, original_thesis,
                original_strategy, bull_case, bear_case, market_regime,
                target_price, stop_loss
            close_price: 可选覆盖，优先使用 position 中的 close_price

        Returns:
            TradeAttribution 完整归因记录
        """
        cp = close_price or position.get("close_price", 0)
        ep = position.get("entry_price", 0)
        pnl = position.get("realized_pnl", 0) or (cp - ep) * position.get("current_qty", 0)
        pnl_pct = ((cp - ep) / ep * 100) if ep > 0 else 0.0

        # 持仓天数
        holding_days = self._calc_holding_days(
            position.get("entry_date", ""),
            position.get("closed_at", ""),
        )

        # 结果判定
        if pnl_pct > 0.5:
            outcome = "win"
        elif pnl_pct < -0.5:
            outcome = "loss"
        else:
            outcome = "breakeven"

        # 规则判定主因
        entry_regime = position.get("market_regime", "")
        close_regime = self._get_latest_regime()
        regime_changed = bool(entry_regime and close_regime and entry_regime != close_regime)

        primary_cause = self._determine_primary_cause(
            position, cp, ep, pnl_pct, holding_days, regime_changed,
        )
        secondary_causes = self._determine_secondary_causes(
            position, cp, ep, pnl_pct, holding_days, regime_changed, primary_cause,
        )

        # 构建基础 attribution
        attr = TradeAttribution(
            attribution_id=f"ATR-{uuid.uuid4().hex[:8].upper()}",
            position_id=position["position_id"],
            symbol=position.get("symbol", ""),
            name=position.get("name", ""),
            entry_price=ep,
            close_price=cp,
            realized_pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            holding_days=holding_days,
            outcome=outcome,
            primary_cause=primary_cause,
            secondary_causes=secondary_causes,
            entry_regime=entry_regime,
            close_regime=close_regime,
            regime_changed=regime_changed,
            strategy_id=position.get("original_strategy", ""),
            original_thesis=position.get("original_thesis", ""),
            created_at=datetime.now().isoformat(),
        )

        # LLM 生成 narrative + lesson
        llm_result = self._llm_generate(attr, position)
        attr.actual_narrative = llm_result.get("actual_narrative", "")
        attr.lesson = llm_result.get("lesson", "")
        attr.should_have = llm_result.get("should_have", "")
        attr.tags = llm_result.get("tags", [])

        self.logger.info(
            "归因完成 %s | %s %s | %s | pnl=%+.2f%% | cause=%s | lesson=%s",
            attr.attribution_id, attr.symbol, attr.name,
            attr.outcome, attr.pnl_pct, attr.primary_cause,
            attr.lesson[:40] if attr.lesson else "N/A",
        )
        return attr

    def attribute_and_save(self, position: dict, close_price: float | None = None) -> TradeAttribution:
        """归因 + 持久化 (attribution + lesson 都写入 DB)。"""
        attr = self.attribute(position, close_price)

        # 保存 attribution
        self.brain.store.save_attribution(attr.to_dict())

        # 提取 lesson 并保存
        if attr.lesson:
            lesson = Lesson(
                lesson_id=f"LSN-{uuid.uuid4().hex[:8].upper()}",
                attribution_id=attr.attribution_id,
                symbol=attr.symbol,
                strategy_id=attr.strategy_id,
                regime=attr.entry_regime,
                outcome=attr.outcome,
                lesson_text=attr.lesson,
                tags=attr.tags,
                created_at=datetime.now().isoformat(),
            )
            self.brain.store.save_lesson(lesson.to_dict())

        # 闭环反馈 — ExperienceLayer 基于 DB 实时聚合，无需额外更新
        # trade_attributions 表写入后，StrategyLedger / StockMemory 自动可查
        self.logger.info(
            "归因已写入 DB — ExperienceLayer 将自动反映: strategy=%s outcome=%s",
            attr.strategy_id, attr.outcome,
        )

        return attr

    # ─────────────────────────────────────────────────────────────────────
    # 规则引擎: 主因判定
    # ─────────────────────────────────────────────────────────────────────

    def _determine_primary_cause(
        self, position: dict, close_price: float, entry_price: float,
        pnl_pct: float, holding_days: int, regime_changed: bool,
    ) -> str:
        target = position.get("target_price") or 0
        stop_loss = position.get("stop_loss") or 0

        # 止盈: 收盘价 >= 目标价的 95%
        if target > 0 and close_price >= target * 0.95 and pnl_pct > 0:
            return "take_profit_triggered"

        # 止损: 收盘价 <= 止损价的 105%
        if stop_loss > 0 and close_price <= stop_loss * 1.05 and pnl_pct < 0:
            return "stop_loss_triggered"

        # Regime 突变导致亏损
        if regime_changed and pnl_pct < -2:
            return "regime_shift"

        # 持仓超时 (persona 设定 1-5 天)
        if holding_days > 5:
            return "held_too_long"

        # 盈利归因
        if pnl_pct > 0:
            return "thesis_correct"

        # 追高: entry 在近期 10 日高点附近 (需要 kline 数据，简化判断)
        # 用 entry_price vs target_price 的位置判断
        if target > 0 and entry_price > 0:
            upside_pct = (target - entry_price) / entry_price
            if upside_pct < 0.03 and pnl_pct < 0:
                return "timing_late"

        # 默认: 选股逻辑错误
        return "thesis_wrong"

    def _determine_secondary_causes(
        self, position: dict, close_price: float, entry_price: float,
        pnl_pct: float, holding_days: int, regime_changed: bool,
        primary_cause: str,
    ) -> list[str]:
        causes = []
        if regime_changed and primary_cause != "regime_shift":
            causes.append("regime_shift")
        if holding_days > 5 and primary_cause != "held_too_long":
            causes.append("held_too_long")
        return causes

    # ─────────────────────────────────────────────────────────────────────
    # LLM 生成 narrative + lesson
    # ─────────────────────────────────────────────────────────────────────

    def _llm_generate(self, attr: TradeAttribution, position: dict) -> dict[str, Any]:
        """调用 LLM 生成叙事 + 教训。失败时返回空结果。"""
        user_msg = ATTRIBUTION_USER_TEMPLATE.format(
            symbol=attr.symbol,
            name=attr.name,
            strategy=position.get("original_strategy", "unknown"),
            strategy_id=attr.strategy_id,
            outcome=attr.outcome,
            pnl_pct=attr.pnl_pct,
            holding_days=attr.holding_days,
            entry_price=attr.entry_price,
            entry_regime=attr.entry_regime or "unknown",
            original_thesis=attr.original_thesis or "无记录",
            bull_case=position.get("bull_case", "无记录"),
            bear_case=position.get("bear_case", "无记录"),
            close_price=attr.close_price,
            close_regime=attr.close_regime or "unknown",
            regime_changed="是" if attr.regime_changed else "否",
            primary_cause=attr.primary_cause,
        )

        try:
            llm = get_llm()
            response = llm.invoke([
                {"role": "system", "content": ATTRIBUTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ])
            return self._parse_json(response.content)
        except Exception as e:
            self.logger.warning("LLM 归因失败: %s — 使用规则默认值", e)
            return {
                "actual_narrative": f"{'盈利' if attr.pnl_pct > 0 else '亏损'}{abs(attr.pnl_pct):.1f}%",
                "lesson": f"{attr.primary_cause}: {attr.symbol}",
                "should_have": "",
                "tags": [attr.primary_cause, attr.outcome],
            }

    # ─────────────────────────────────────────────────────────────────────
    # 辅助方法
    # ─────────────────────────────────────────────────────────────────────

    def _get_latest_regime(self) -> str:
        """获取最近一次 market regime。"""
        regime = self.brain.store.latest_market_regime()
        return regime.get("regime", "") if regime else ""

    def _calc_holding_days(self, entry_date: str, closed_at: str) -> int:
        try:
            entry = datetime.fromisoformat(entry_date[:10])
            close = datetime.fromisoformat(closed_at[:10]) if closed_at else datetime.now()
            return max(1, (close - entry).days)
        except (ValueError, TypeError):
            return 1

    def _parse_json(self, content: str) -> dict[str, Any]:
        text = content.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            l = text.find("{")
            r = text.rfind("}")
            if l >= 0 and r > l:
                return json.loads(text[l:r + 1])
            return {}
