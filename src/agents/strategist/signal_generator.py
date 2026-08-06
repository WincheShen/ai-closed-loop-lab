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
    "底部启动": "bottom_reversal",
    "底部放量": "bottom_reversal",
    "连跌反转": "bottom_reversal",
    "价值投资": "value_investing",
    "优质低估": "value_investing",
    "护城河": "moat_quality",
    "高ROE": "value_investing",
    "分红稳定": "value_investing",
    "主力吸筹": "institutional_accumulation",
}

# 绝对禁用策略黑名单 — 无论 LLM 输出什么，含有这些关键词的信号一律拦截
_BANNED_STRATEGY_KEYWORDS = {"放量突破", "volume_breakout"}


def _infer_strategy_id(strategy_name: str) -> str:
    if not strategy_name:
        return "unknown"
    name = strategy_name.strip()
    if name in _STRATEGY_NAME_TO_ID:
        return _STRATEGY_NAME_TO_ID[name]
    for key, sid in _STRATEGY_NAME_TO_ID.items():
        if key in name:
            return sid
    return "unknown"


def _normalize_strategy_name(raw: str) -> str:
    """将 LLM 输出的策略名规范化到标准名称，减少 40+ 变体。

    例：
    - "价值投资/高ROE/护城河/分红稳定" → "价值投资"
    - "护城河+高ROE+合理估值" → "价值投资"
    - "热点板块前排回踩" → "热点板块前排回踩" (保留)
    """
    if not raw:
        return raw
    sid = _infer_strategy_id(raw)
    _ID_TO_CANONICAL = {
        "hot_sector_pullback": "热点板块前排回踩",
        "volume_breakout": "放量突破",
        "defensive_bluechip": "防守蓝筹",
        "mean_reversion": "均值回归",
        "bottom_reversal": "底部启动",
        "value_investing": "价值投资",
        "moat_quality": "价值投资",
        "institutional_accumulation": "主力吸筹",
    }
    return _ID_TO_CANONICAL.get(sid, raw)


STRATEGIST_SYSTEM_PROMPT = """\
你是一位 Cognitive Agent 的交易决策者。你必须严格遵守"投资人格"约束，
并根据"当日市场作战指令"调整决策。不要每只票都给 BUY，宁可错过不要做错。

## 决策原则
1. 必须先读懂今日 market_regime 和 recommended_posture，再判断个股
2. 在禁用策略 (forbidden) 上不要 BUY
3. 在 degraded 策略上要明显降低仓位
4. 风险收益比 < 1.2 一律 PASS
5. 弱势市场 (bear/panic) 必须更严格，能不交易就不交易

## 禁用策略（已证明负期望，绝对不要使用）
- 放量突破 (volume_breakout) — 历史胜率仅 11%，平均亏损 -25%，累计亏损 ¥28,052
- ⚠️ 任何策略名包含「放量突破」「突破前高」「追涨」的，一律禁止！
- 如果你想推荐的标的只适合放量突破，请直接 PASS

## 推荐策略（只能从以下策略中选择）
- 热点板块前排回踩: 板块强势 + 龙头回踩支撑 + 量能不萎缩（历史胜率最高）
- 主力吸筹: 龙虎榜机构净买入 + 换手率放大 + 价格未涨（底部建仓形态）
- 底部启动: 连跌3日以上 + 放量收阳 + MA5上穿MA10（金叉）
- 防守蓝筹: 低估值 + 高股息 + 稳定现金流
- 均值回归: 短期超跌 + 技术支撑位 + 量能萎缩到极致后放量

## 5-Gate 结构化检查清单（必须逐项评估）
在给出 BUY/PASS 之前，你必须先对以下 5 道关卡逐项打分:
- **pass**: 该条件明确满足（2分）
- **conditional**: 勉强满足或有保留（1分）
- **fail**: 明确不满足（0分）

| Gate | 检查项 | 评判标准 |
|------|--------|----------|
| G1 趋势确认 | MA排列+价格位置+趋势方向 | pass: MA5>MA10>MA20多头排列或底部金叉; fail: 均线空头下行 |
| G2 量价配合 | 量比+换手率+资金流向 | pass: 量比>1.2且主力净流入; fail: 缩量或主力大幅流出 |
| G3 板块共振 | 所属板块是否在热点+板块强度 | pass: 在当日Top5热点板块; conditional: 板块数据缺失(可跳过); fail: 板块明显走弱 |
| G4 风险收益比 | (目标价-入场价)/(入场价-止损价) | pass: >=2.0; conditional: 1.2-2.0; fail: <1.2 |
| G5 时机匹配 | 与regime/posture是否兼容 | pass: posture=attack/selective_attack; conditional: observe但个股极强; fail: exit/defend |

**硬规则**:
- 任何一项 = fail → 必须 PASS，不买
- 总分 <= 5 → 仓位不超过 3%
- 总分 6-7 → 仓位 3%-5%
- 总分 8-10 → 仓位 5%-10%

## 输出格式（严格 JSON）
```json
{
    "action": "BUY 或 PASS",
    "checklist": {
        "G1_trend": "pass/conditional/fail",
        "G2_volume": "pass/conditional/fail",
        "G3_sector": "pass/conditional/fail",
        "G4_risk_reward": "pass/conditional/fail",
        "G5_timing": "pass/conditional/fail"
    },
    "entry_condition": "immediate/breakout/pullback",
    "entry_price": 建议入场价,
    "target_price": 目标价,
    "stop_loss": 止损价,
    "position_pct": 建议仓位比例(0.02-0.10, 必须符合上面的总分仓位约束),
    "strategy": "策略名称(只能从推荐策略中选: 热点板块前排回踩/主力吸筹/底部启动/防守蓝筹/均值回归，禁止使用放量突破)",
    "confidence": 0.0到1.0,
    "rationale": "完整的买入或不买逻辑(2-3句话, 必须引用 regime/posture 和 checklist 结果)",
    "bull_case": "最大的看多理由",
    "bear_case": "最大的风险点"
}
```

## entry_condition 说明
- **immediate**: 当前价格已在合理入场区间，可以直接买入
- **breakout**: 需要等股价突破 entry_price 后再买入（如突破前高、突破压力位）
- **pullback**: 需要等股价回调到 entry_price 附近再买入（如回踩支撑位）
- 如果 entry_price 高于当前价格且需要等突破，必须选 breakout
- 如果 entry_price 低于当前价格且需要等回调，必须选 pullback
- 如果当前价格就是合理入场价，选 immediate
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

## 决策提示
- 如果热点板块为"无"或为空，说明当前板块数据不可用，请**基于技术面、资金面、市场 regime** 做决策，不要因为"无板块支撑"而拒绝所有标的
- 技术面重点：底部启动形态（连跌后放量收阳+金叉）、趋势形态、量价配合
- 资金面重点：主力净流入、换手率、成交额
- 结合今日 market_regime 和 posture 判断是否适合交易
{bottom_reversal_hint}
{institutional_accumulation_hint}

请基于以上信息，特别是结合 regime 和 persona 给出决策。
"""


# ---------------------------------------------------------------------------
# 价值投资专用 Prompt（段永平 / 巴菲特人格）
# ---------------------------------------------------------------------------

VALUE_INVESTING_SYSTEM_PROMPT = """\
你是一位价值投资决策者。你必须严格遵守"投资人格"约束，
以企业内在价值为核心标准，而非短期技术走势。宁可错过不要做错。

## 决策原则
1. **企业质量优先**：ROE > 15% 且稳定、负债率低、分红好的才值得关注
2. **估值安全边际**：PE 必须在合理区间内，PB 不过高，相对历史估值有折价
3. **护城河与持续性**：品牌溢价、网络效应、转换成本、规模经济 — 至少满足一项
4. **管理层质量**：诚信、回购、增持 = 加分；减持、高管离职 = 减分
5. **不追热点**：不因短期涨跌或热门板块做决策，只关注企业长期价值
6. 弱势市场是好机会，优质资产被错杀时应考虑买入

## 决策框架
- **BUY**: 企业质量优秀 + 估值合理/低估 + 有安全边际 + 符合 persona 偏好
- **PASS**: 基本面不达标 / 估值偏高 / 商业模式看不懂 / 管理层不可信

## 5-Gate 结构化检查清单（必须逐项评估）
在给出 BUY/PASS 之前，你必须先对以下 5 道关卡逐项打分:
- **pass**: 该条件明确满足（2分）
- **conditional**: 勉强满足或有保留（1分）
- **fail**: 明确不满足（0分）

| Gate | 检查项 | 评判标准 |
|------|--------|----------|
| G1 好生意 | ROE+毛利率+现金流 | pass: ROE>15%且稳定,FCF为正; conditional: ROE 10-15%或周期性波动; fail: ROE<10%或FCF持续为负 |
| G2 护城河 | 品牌/转换成本/网络效应/规模 | pass: 至少2项护城河且在加宽; conditional: 1项但稳定; fail: 无明显护城河或正在被侵蚀 |
| G3 安全边际 | PE/PB历史分位+股息率 | pass: PE<历史中位数且股息率>2%; conditional: PE合理但无明显折价; fail: PE明显高估 |
| G4 管理层 | 诚信度+资本配置+利益一致性 | pass: 有增持/回购记录,资本配置理性; conditional: 中性,无明显加减分; fail: 有减持/财务疑点/治理问题 |
| G5 确定性 | 商业模式可理解+10年可预测 | pass: 一句话说清生意,10年不会被颠覆; conditional: 生意可理解但有不确定性; fail: 看不懂或变化太快 |

**硬规则**:
- 任何一项 = fail → 必须 PASS，不买
- 总分 <= 5 → 仓位 8%（最低底仓）
- 总分 6-7 → 仓位 8%-15%
- 总分 8-10 → 仓位 15%-30%（重仓优质标的）

## 输出格式（严格 JSON）
```json
{
    "action": "BUY 或 PASS",
    "checklist": {
        "G1_business": "pass/conditional/fail",
        "G2_moat": "pass/conditional/fail",
        "G3_margin_of_safety": "pass/conditional/fail",
        "G4_management": "pass/conditional/fail",
        "G5_certainty": "pass/conditional/fail"
    },
    "entry_condition": "immediate(估值已进入安全区) 或 pullback(等待回调到更安全价位)",
    "entry_price": 建议入场价,
    "target_price": 目标价(基于内在价值估算),
    "stop_loss": 止损价(基于安全边际下限),
    "position_pct": 建议仓位比例(0.08-0.30, 必须符合上面的总分仓位约束),
    "strategy": "策略名称(如 价值投资/优质低估/护城河/高ROE/分红稳定)",
    "confidence": 0.0到1.0,
    "rationale": "完整的投资逻辑(2-3句话,必须引用基本面指标和 checklist 结果)",
    "bull_case": "最核心的投资价值",
    "bear_case": "最大的风险点"
}
```

## entry_condition 说明
- **immediate**: 当前估值已有足够安全边际，可以建仓
- **pullback**: 企业质量好但估值需要更大折价，等回调到目标价再买
- 价值投资不用 breakout（不追突破）
"""

VALUE_INVESTING_USER_TEMPLATE = """\
{persona_block}

## 今日市场环境 (来自 MarketBrain)
- regime: {regime}  | posture: {posture}  | risk_appetite: {risk_appetite}
- 总仓位上限: {max_total_pos:.0%}
- 摘要: {regime_summary}
- 注意: 价值投资不受短期 regime 过多影响，弱市反而可能是建仓好时机

## 候选股票
- 代码: {symbol}
- 名称: {name}
- 所属行业: {sector}

## 入选理由
{hot_reason}

## 基本面数据（核心决策依据）
- PE(TTM): {pe_ttm}
- PB: {pb}
- 市值: {market_cap}亿
- **ROE**: {roe}
- **资产负债率**: {debt_to_equity}
- **股息率**: {dividend_yield}
- **自由现金流收益率**: {fcf_yield}

## 估值参考
- 当前价格: {price}
- 今日涨跌: {change_pct}%
- 近60日涨幅: {recent_60d}%
- 价格 vs MA60: {price_vs_ma60}
- 近60日高点: {high_60d} | 低点: {low_60d}

## 技术辅助（仅做参考，不作为主要决策依据）
- MA20: {ma20} | MA60: {ma60}
- 趋势: {trend}
- 量比: {vol_ratio}

## 决策提示
- 重点关注：ROE是否稳定在15%以上、负债率是否健康、现金流是否充裕
- 估值：PE 是否在历史合理区间的下半部分
- 分红：股息率是否有吸引力
- 不要因为短期下跌就恐慌 PASS，反而可能是机会
- 不要因为热点或涨势就盲目 BUY，必须基于企业价值

请基于以上基本面数据和投资人格给出决策。
"""


class StrategistEngine:

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

    def _is_value_persona(self) -> bool:
        """判断当前人格是否为价值投资风格。"""
        pid = self.persona.id.lower()
        return any(k in pid for k in ("duan", "buffett", "value"))

    def _calculate_position_pct(
        self, strategy_name: str, llm_suggestion: float,
    ) -> float:
        """基于 Kelly Criterion 动态计算仓位比例。

        优先级: Kelly (有足够样本时) > LLM suggestion > 默认值
        始终受 persona max_single_position_pct 和 min floor 约束。
        """
        is_value = self._is_value_persona()
        min_floor = 0.08 if is_value else 0.02
        max_cap = (
            self.persona.max_single_position_pct
            if hasattr(self.persona, 'max_single_position_pct')
            else self.config.get("max_position_pct", 0.10)
        )

        # 尝试 Kelly 计算
        try:
            from src.agents.strategist.kelly_sizer import get_kelly_sizer
            sizer = get_kelly_sizer()
            regime = self.market_regime.get("regime")
            kelly_result = sizer.calculate(
                strategy_id=strategy_name, regime=regime, is_value=is_value,
            )
            if kelly_result.method == "kelly":
                # Kelly 有效：使用 Kelly 仓位
                position_pct = kelly_result.final_fraction
                self.logger.info(
                    "[Kelly] 策略=%s regime=%s → %.1f%% (WR=%.0f%% b=%.2f)",
                    strategy_name, regime,
                    position_pct * 100,
                    kelly_result.stats.get("win_rate", 0) * 100,
                    kelly_result.stats.get("avg_win_pct", 0)
                    / abs(kelly_result.stats.get("avg_loss_pct", 1) or 1),
                )
            else:
                # Kelly 样本不足：用 LLM 建议作为参考
                position_pct = llm_suggestion
        except Exception as e:
            self.logger.debug("Kelly 计算失败，使用 LLM 建议: %s", e)
            position_pct = llm_suggestion

        # 约束: [min_floor, max_cap]
        return round(min(max(position_pct, min_floor), max_cap), 4)

    def _lessons_block(self) -> str:
        regime = self.market_regime.get("regime")
        try:
            lessons = self.brain.store.get_recent_lessons(regime=regime, limit=5)
        except Exception:
            lessons = []

        if not lessons:
            return ""

        lines = []
        for l in lessons:
            txt = l.get("lesson_text") or l.get("lesson") or ""
            symbol = l.get("symbol", "")
            strategy = l.get("strategy_id", "")
            outcome = l.get("outcome", "")
            if txt:
                tag = f"({'盈' if outcome == 'win' else '亏' if outcome == 'loss' else '平'})"
                lines.append(f"- [{strategy}] {symbol}{tag}: {txt}")
                try:
                    self.brain.store.increment_lesson_cited(l["lesson_id"])
                except Exception:
                    pass

        if not lines:
            return ""

        return "\n\n## 历史交易教训（避免重复错误）\n" + "\n".join(lines)

    def _evolution_block(self) -> str:
        """从 ExperienceLayer 生成策略表现反馈片段注入 prompt。

        替代旧的 prompt_weights.json 机制，直接从 trade_attributions 表聚合。
        """
        try:
            from src.experience_layer import get_experience
            exp = get_experience()
            regime = self.market_regime.get("regime") if self.market_regime else None
            return exp.strategy_prompt_block(regime=regime)
        except Exception:
            return ""

    def _stock_memory_block(self, symbol: str) -> str:
        """从 ExperienceLayer 生成个股交易记忆片段注入 prompt。"""
        try:
            from src.experience_layer import get_experience
            exp = get_experience()
            return exp.stock_prompt_block(symbol)
        except Exception:
            return ""

    def _meta_rules_block(self) -> str:
        """从 MetaRuleSynthesizer 获取当前生效的元规则并注入 prompt。"""
        try:
            from src.experience_layer.meta_rule_synthesizer import get_synthesizer
            return get_synthesizer().generate_prompt_block()
        except Exception:
            return ""

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

    def _build_short_term_msg(self, candidate: StockCandidate, kline: dict, fund: dict) -> str:
        """构建短线人格的 user message（技术面+资金面为主）。"""
        # 底部启动形态提示
        br_signal = kline.get("bottom_reversal_signal")
        if br_signal:
            br_hint = (
                f"\n⚡ **底部启动形态已检测** (得分 {br_signal['score']}/3): "
                f"{br_signal['detail']}\n"
                f"  → 建议策略: 底部启动 | entry_condition: immediate"
            )
        else:
            br_hint = ""

        # 主力吸筹形态提示
        ia_signal = kline.get("institutional_accumulation_signal")
        if ia_signal:
            ia_hint = (
                f"\n🏦 **主力吸筹形态已检测**: "
                f"{ia_signal['detail']}\n"
                f"  → 建议策略: 主力吸筹 | 机构底部建仓，价格未涨，关注启动信号"
            )
        else:
            ia_hint = ""

        return STRATEGIST_USER_TEMPLATE.format(
            persona_block=self._persona_block(),
            symbol=candidate["symbol"],
            name=candidate["name"],
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
            inflow_wan=round((fund or {}).get("main_net_inflow", 0) / 1e4, 1),
            turnover_yi=round((fund or {}).get("turnover", 0) / 1e8, 2),
            turnover_rate=(fund or {}).get("turnover_rate", 0),
            hot_sectors=", ".join(self.hot_sectors) if self.hot_sectors else "无",
            bottom_reversal_hint=br_hint,
            institutional_accumulation_hint=ia_hint,
            **self._regime_kwargs(),
        )

    def _build_value_investing_msg(self, candidate: StockCandidate, kline: dict, fund: dict) -> str:
        """构建价值投资人格的 user message（基本面为主 + 估值参考）。"""
        # 从 kline_summary 中提取基本面字段
        roe = kline.get("roe")
        debt_to_equity = kline.get("debt_to_equity")
        dividend_yield = kline.get("dividend_yield")
        fcf_yield = kline.get("fcf_yield")

        def fmt(v, suffix: str = "") -> str:
            return f"{v}{suffix}" if v is not None else "N/A"

        regime_kw = self._regime_kwargs()
        return VALUE_INVESTING_USER_TEMPLATE.format(
            persona_block=self._persona_block(),
            symbol=candidate["symbol"],
            name=candidate["name"],
            sector=candidate.get("sector", "未知"),
            hot_reason="\n".join(
                f"- {r}" for r in candidate.get("hot_reason", [])
            ),
            price=kline.get("current_price", 0),
            change_pct=kline.get("change_pct", 0),
            pe_ttm=kline.get("pe_ttm") or "N/A",
            pb=kline.get("pb") or "N/A",
            market_cap=kline.get("market_cap_yi") or "N/A",
            roe=fmt(roe, "%"),
            debt_to_equity=fmt(
                round(debt_to_equity * 100, 1) if debt_to_equity is not None else None, "%"
            ),
            dividend_yield=fmt(dividend_yield, "%"),
            fcf_yield=fmt(fcf_yield, "%"),
            recent_60d=kline.get("recent_5d_change_pct", "N/A"),
            price_vs_ma60=kline.get("price_vs_ma20", "N/A"),
            high_60d=kline.get("recent_high_10d", "N/A"),
            low_60d=kline.get("recent_low_10d", "N/A"),
            ma20=kline.get("ma20", "N/A"),
            ma60=kline.get("ma20", "N/A"),
            trend=kline.get("trend", "N/A"),
            vol_ratio=kline.get("vol_ratio", "N/A"),
            regime=regime_kw["regime"],
            posture=regime_kw["posture"],
            risk_appetite=regime_kw["risk_appetite"],
            max_total_pos=regime_kw["max_total_pos"],
            regime_summary=regime_kw["regime_summary"],
        )

    def analyze_candidate(self, candidate: StockCandidate) -> TradeSignal | None:
        symbol = candidate["symbol"]
        name = candidate["name"]
        kline = candidate.get("kline_summary", {})
        fund = candidate.get("fund_flow", {})

        if self._is_value_persona():
            user_msg = self._build_value_investing_msg(candidate, kline, fund)
            system_prompt = VALUE_INVESTING_SYSTEM_PROMPT
        else:
            user_msg = self._build_short_term_msg(candidate, kline, fund)
            system_prompt = STRATEGIST_SYSTEM_PROMPT

        user_msg += self._lessons_block()
        user_msg += self._evolution_block()
        user_msg += self._stock_memory_block(symbol)
        user_msg += self._meta_rules_block()

        try:
            llm = get_llm()
            messages = [
                {"role": "system", "content": system_prompt},
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

        # ── Checklist Gate 硬拦截 ──
        checklist = result.get("checklist") or {}
        if checklist:
            gate_scores = {"pass": 2, "conditional": 1, "fail": 0}
            has_fail = False
            total_score = 0
            gate_details = []
            for gate_key, gate_val in checklist.items():
                val = str(gate_val).lower().strip()
                score = gate_scores.get(val, 1)  # 未知值视为 conditional
                total_score += score
                gate_details.append(f"{gate_key}={val}")
                if val == "fail":
                    has_fail = True

            if has_fail:
                self.logger.warning(
                    "[%s %s] CHECKLIST REJECT — 存在 fail 项: %s",
                    symbol, name, ", ".join(gate_details),
                )
                return None

            # 根据总分限制仓位
            is_value = self._is_value_persona()
            llm_pct = result.get("position_pct", 0.05)
            if is_value:
                if total_score <= 5:
                    max_pct = 0.08
                elif total_score <= 7:
                    max_pct = 0.15
                else:
                    max_pct = 0.30
            else:
                if total_score <= 5:
                    max_pct = 0.03
                elif total_score <= 7:
                    max_pct = 0.05
                else:
                    max_pct = 0.10

            if llm_pct > max_pct:
                self.logger.info(
                    "[%s] Checklist 总分=%d，仓位 %.0f%% → %.0f%% (上限约束)",
                    symbol, total_score, llm_pct * 100, max_pct * 100,
                )
                result["position_pct"] = max_pct

            self.logger.info(
                "[%s %s] Checklist: %s | 总分=%d/10",
                symbol, name, ", ".join(gate_details), total_score,
            )

        # ── P1 硬拦截: 绝对禁用策略（无论 LLM 怎么说都拒绝）──
        strategy_name = result.get("strategy", "")
        if any(kw in strategy_name for kw in _BANNED_STRATEGY_KEYWORDS):
            self.logger.warning(
                "[%s %s] BLOCKED — LLM 输出被禁策略「%s」，强制 PASS",
                symbol, name, strategy_name,
            )
            return None

        # 硬门槛：若 LLM 选择的策略在当前 regime 下被 ExperienceLayer 标记为 SUSPEND
        # 直接拒绝，不由 LLM 判断（数据说话）
        strategy_id_check = _infer_strategy_id(strategy_name)
        current_regime = self.market_regime.get("regime", "")
        if strategy_id_check and strategy_id_check != "unknown" and current_regime:
            try:
                from src.experience_layer import get_experience
                exp = get_experience()
                stats = exp.get_strategy_stats(strategy_id_check, regime=current_regime)
                if stats and stats.get("recommendation") == "SUSPEND":
                    self.logger.warning(
                        "[%s %s] PASS — 策略 %s 在 %s 环境下已被暂停 (胜率%.0f%%, 样本%d)",
                        symbol, name, strategy_id_check, current_regime,
                        stats["win_rate"] * 100, stats["total_trades"],
                    )
                    return None
            except Exception:
                self.logger.warning(
                    "[%s %s] SUSPEND 检查异常，安全起见拒绝信号",
                    symbol, name, exc_info=True,
                )
                return None

        current_price = kline.get("current_price", 0)
        entry_price = result.get("entry_price") or current_price
        if entry_price <= 0:
            entry_price = current_price

        # 入场价合理性校验 — 防止 LLM 返回偏离当前价过大的值（如复权价/错误价）
        if current_price > 0 and entry_price > 0:
            deviation = abs(entry_price - current_price) / current_price
            if deviation > 0.20:
                self.logger.warning(
                    "[%s] LLM entry_price=%.2f 偏离 current_price=%.2f 达 %.0f%%，修正为当前价",
                    symbol, entry_price, current_price, deviation * 100,
                )
                entry_price = current_price
        stop_loss_pct = self.persona.default_stop_loss_pct if hasattr(
            self.persona, 'default_stop_loss_pct'
        ) else self.config.get("default_stop_loss_pct", 0.05)

        # ATR-based 动态止损保护：如果 LLM 给的止损距离 < 1.5×ATR，认为过紧（会被日内噪音扫出）
        # 依据: 历史数据 53% 退出是止损，一部分是被噪音扫出而非真正破位
        llm_stop_loss = result.get("stop_loss")
        atr_pct = kline.get("atr_pct", 0)
        if llm_stop_loss and llm_stop_loss > 0 and entry_price > 0 and atr_pct > 0:
            llm_stop_distance_pct = (entry_price - llm_stop_loss) / entry_price * 100
            min_stop_distance_pct = atr_pct * 1.5  # 1.5×ATR 是硬下限
            if 0 < llm_stop_distance_pct < min_stop_distance_pct:
                self.logger.info(
                    "[%s] 止损保护: LLM止损距离%.1f%% < 1.5×ATR(%.1f%%)，放宽到 1.5×ATR",
                    symbol, llm_stop_distance_pct, min_stop_distance_pct,
                )
                # 用 1.5×ATR 距离作为止损（但不超过 8% 硬上限）
                effective_stop_pct = min(min_stop_distance_pct, 8.0) / 100
                result["stop_loss"] = entry_price * (1 - effective_stop_pct)

        # 判断入场条件类型
        llm_condition = result.get("entry_condition", "").lower()
        if llm_condition in ("breakout", "pullback"):
            entry_condition = llm_condition
        elif current_price > 0 and entry_price > current_price * 1.005:
            entry_condition = "breakout"
        elif current_price > 0 and entry_price < current_price * 0.995:
            entry_condition = "pullback"
        else:
            entry_condition = "immediate"

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
            "position_pct": self._calculate_position_pct(
                strategy_name=_normalize_strategy_name(result.get("strategy", "LLM综合分析")),
                llm_suggestion=result.get("position_pct", 0.08),
            ),
            "strategy": _normalize_strategy_name(result.get("strategy", "LLM综合分析")),
            "rationale": result.get("rationale", ""),
            "timestamp": datetime.now().isoformat(),
            "expiry": (datetime.now() + timedelta(
                days=self.persona.preferred_holding_days[-1] if hasattr(
                    self.persona, 'preferred_holding_days'
                ) and self.persona.preferred_holding_days else 5
            )).isoformat(),
        }

        signal["name"] = name
        signal["sector"] = candidate.get("sector", "")
        signal["bull_case"] = result.get("bull_case", "")
        signal["bear_case"] = result.get("bear_case", "")
        signal["confidence"] = result.get("confidence", 0.5)
        signal["strategy_id"] = _infer_strategy_id(signal["strategy"])
        signal["market_regime"] = self.market_regime.get("regime", "")
        signal["persona_version"] = self.persona.persona_version
        signal["persona_id"] = self.persona.id  # P1: 人格标识
        signal["entry_condition"] = entry_condition
        signal["current_price"] = round(current_price, 2)

        condition_label = {
            "immediate": "立即",
            "breakout": f"突破{entry_price:.2f}",
            "pullback": f"回调{entry_price:.2f}",
        }.get(entry_condition, entry_condition)
        self.logger.info(
            "生成信号 %s | %s %s | 策略=%s (id=%s) | regime=%s | 条件=%s | 入场=%.2f | 当前=%.2f | 止损=%.2f | 目标=%.2f | 置信=%.0f%%",
            signal["signal_id"], symbol, name, signal["strategy"],
            signal["strategy_id"], signal["market_regime"],
            condition_label,
            signal["entry_price"], current_price, signal["stop_loss"], signal["target_price"],
            result.get("confidence", 0) * 100,
        )
        return signal

    def generate_signals(
        self, candidates: list[StockCandidate],
    ) -> list[TradeSignal]:
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
    session_id = state["session_id"]
    hot_sectors = state.get("hot_sectors", [])
    market_regime = state.get("market_regime") or {}
    persona_id = state.get("persona_id")
    persona = get_persona(persona_id=persona_id)
    engine = StrategistEngine(
        session_id,
        hot_sectors=hot_sectors,
        persona=persona,
        market_regime=market_regime,
    )

    # ── P4: 短线人格在 bear/panic 下跳过信号生成（节省 LLM 调用）──
    regime_label = market_regime.get("regime", "")
    posture = market_regime.get("recommended_posture", "")
    if not engine._is_value_persona() and regime_label in ("bear", "panic"):
        logger.info(
            "[Strategist] 短线人格在 %s/%s 市场下跳过信号生成 (节省 LLM 调用)",
            regime_label, posture,
        )
        return {
            "trade_signals": [],
            "risk_assessment": {"skipped": f"short_term_skip_{regime_label}"},
            "logs": state.get("logs", []) + [
                f"[Strategist] regime={regime_label} 短线人格跳过 (bear/panic 下不生成信号)"
            ],
        }

    candidates = list(state.get("target_stocks", []))

    # --- 合并自选股池中已触发的标的 ---
    triggered_candidates = _merge_triggered_watchlist(engine, candidates)
    candidates = triggered_candidates + candidates

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

    triggered_count = len(triggered_candidates)
    return {
        "trade_signals": signals,
        "risk_assessment": risk,
        "logs": state.get("logs", []) + [
            f"[Strategist] regime={market_regime.get('regime', 'n/a')} 分析 "
            f"{min(len(candidates), MAX_LLM_ANALYSIS)} 只"
            f"(含{triggered_count}只自选触发), "
            f"生成 {len(signals)} 条买入信号, 风控={risk['risk_level']}"
        ],
    }


def _merge_triggered_watchlist(
    engine: StrategistEngine,
    existing: list[StockCandidate],
) -> list[StockCandidate]:
    """读取自选股池中 status=triggered 的标的，转换为 StockCandidate 并排在最前面。

    优先级高于 Scanner 新选出的候选，确保 LLM 分析额度优先给已触发的自选股。
    已在现有候选列表中的 symbol 会跳过去重。
    """
    try:
        brain = get_central_brain()
        triggered = brain.store.get_watchlist(status="triggered")
    except Exception:
        engine.logger.warning("读取 triggered watchlist 失败", exc_info=True)
        return []

    if not triggered:
        return []

    existing_symbols = {c["symbol"] for c in existing}
    result: list[StockCandidate] = []

    for item in triggered:
        symbol = item.get("symbol", "")
        if not symbol or symbol in existing_symbols:
            continue

        candidate: StockCandidate = {
            "symbol": symbol,
            "name": item.get("name", ""),
            "qlib_score": 5.0,  # 高分以确保排在前面
            "sector": item.get("sector", ""),
            "hot_reason": [
                f"自选股触发: {item.get('entry_condition', '')}",
                item.get("thesis", ""),
            ],
            "kline_summary": {
                "current_price": item.get("last_price") or 0,
            },
            "fund_flow": None,
            "dragon_tiger": None,
        }
        result.append(candidate)
        engine.logger.info(
            "[WATCHLIST→STRATEGIST] 自选股触发纳入分析: %s %s | 条件=%s",
            symbol, item.get("name", ""), item.get("entry_condition", ""),
        )

        # 标记为已消费，避免下次重复处理
        try:
            brain.store.remove_from_watchlist(
                item["watch_id"], reason="triggered_consumed_by_strategist",
            )
        except Exception:
            pass

    if result:
        engine.logger.info(
            "[WATCHLIST] 共 %d 只自选股触发，优先纳入 Strategist 分析", len(result),
        )

    return result
