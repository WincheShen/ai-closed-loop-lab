"""Position Review Agent — 盘中持仓复审。

每 30 分钟对每只持仓股执行一次轻量 LLM 审视：
1. 拉取最新盘中走势
2. 对比原始买入 thesis
3. 判断是否需要调整

输出动作: HOLD / ADD / REDUCE / EXIT
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Literal

from src.agents.reviewer.hard_rules import evaluate_hard_exit
from src.central_brain import get_central_brain
from src.infra.model_adapter import get_llm
from src.stock_analyzer.data_source.intraday_client import IntradayClient
from src.stock_analyzer.data_source.market_summary import summarize_intraday

logger = logging.getLogger(__name__)

ReviewAction = Literal["HOLD", "ADD", "REDUCE", "EXIT"]

REVIEW_SYSTEM_PROMPT = """\
你是一位专业的持仓复审分析师。你的职责是根据「原始买入逻辑」和「最新盘中走势」，
判断当前持仓是否需要调整。

## 你的决策框架

1. **HOLD** — 原始thesis仍然成立，走势未偏离预期，继续持有
2. **ADD** — 走势验证了thesis且出现更优入场点（回调到支撑位），建议加仓
3. **REDUCE** — 部分获利了结或风险信号出现（接近目标价/量能异常/技术信号恶化），建议减仓
4. **EXIT** — thesis被证伪或触发止损条件，建议清仓

## 关键原则

- 不要因为短期波动就推翻长期thesis，区分「噪音」和「信号」
- 如果走势平淡、thesis未变，HOLD是正确答案——不需要为了显得在工作而给出动作
- 浮盈不是卖出的理由，除非接近目标价或出现明确的转弱信号
- 浮亏不是加仓的理由，除非你能解释为什么原始thesis仍然成立
- 关注量价关系：放量上涨是好信号，缩量上涨要警惕；放量下跌是坏信号

## 输出格式（严格JSON）

```json
{
  "action": "HOLD|ADD|REDUCE|EXIT",
  "confidence": 0.0-1.0,
  "reason": "一句话说明决策依据",
  "thesis_status": "intact|weakened|invalidated",
  "key_observation": "你从走势中看到的最重要的一个事实",
  "risk_flag": "如有风险信号在此说明，否则留空"
}
```
"""

REVIEW_USER_TEMPLATE = """\
## 持仓信息
- 股票: {symbol} {name}
- 方向: {side}
- 成本价: {entry_price:.2f}
- 持仓量: {qty} 股
- 入场日期: {entry_date}
- 目标价: {target_price}
- 止损价: {stop_loss}

## 原始买入逻辑
策略: {strategy}
分析: {thesis}
看多理由: {bull_case}
看空风险: {bear_case}
{force_review_section}
## 历史复审记录
{review_history}

## 最新盘中走势
{market_summary}

请根据以上信息给出你的复审判断。
"""

FORCE_REVIEW_TEMPLATE = """\
## ⚠️ 强制复审提醒
{force_review_reason}

注意：此持仓已超出正常持有周期，请更严格地审视继续持有的理由。
除非有非常明确的上涨催化剂，否则应倾向于 REDUCE 或 EXIT。
"""


class PositionReviewAgent:
    """轻量级 LLM 持仓复审 Agent。"""

    def __init__(self, model_name: str | None = None, persona_id: str | None = None) -> None:
        self.brain = get_central_brain()
        self.intraday = IntradayClient(allow_mock_fallback=True)
        self.model_name = model_name
        self.persona_id = persona_id

    def review_position(self, position: dict, force_review_reason: str | None = None) -> dict:
        """复审单只持仓，返回决策结果。

        Args:
            position: 从 store.list_open_positions() 获取的持仓 dict
            force_review_reason: 强制复审原因（如超期持仓），注入 prompt 使 LLM 更积极建议卖出

        Returns:
            {"action": str, "confidence": float, "reason": str, ...}
        """
        symbol = position["symbol"]
        name = position.get("name", "")
        entry_price = position["entry_price"]

        # 1. Fetch intraday data
        snapshot = self.intraday.fetch_intraday_snapshot(
            symbol, name=name, period="30", bar_limit=16,
        )

        # 1.4 数据防护 — mock / 缺失 / 异常偏离的价格禁止触发任何交易动作
        # (2026-09-22 事故: 真实源失败降级 mock 且 is_mock 误标，600519 等 6 持仓被假价强平)
        current_price = snapshot.current_price or 0.0
        if getattr(snapshot, "is_mock", False) or not snapshot.bars or current_price <= 0:
            return self._skip_review(
                position,
                f"盘中行情缺失或已降级为 mock 数据(source={getattr(self.intraday, '_last_source', 'unknown')})",
                reference_price=entry_price,
                anomaly_price=current_price,
            )

        reference = self._last_known_price(position) or entry_price
        if reference > 0:
            deviation = abs(current_price - reference) / reference
            if deviation > 0.30:
                return self._skip_review(
                    position,
                    f"当前价 {current_price:.2f} 偏离上次有效价 {reference:.2f} 达 {deviation:.0%}，疑似行情数据异常",
                    reference_price=reference,
                    anomaly_price=current_price,
                )

        # 1.5 Pre-LLM 硬止损检查 — 价格已破止损时跳过 LLM 直接 EXIT（省钱+快速）
        pnl_pct = (current_price / entry_price - 1) * 100 if entry_price > 0 else 0
        if position.get("side") == "short":
            pnl_pct = -pnl_pct

        pre_llm_result = self._pre_llm_hard_check(position, current_price, pnl_pct)
        if pre_llm_result is not None:
            logger.info(
                "[%s %s] Pre-LLM 硬规则触发: %s — %s (跳过LLM调用)",
                symbol, name, pre_llm_result["action"], pre_llm_result["reason"],
            )
            # 直接持久化并返回，不调用 LLM
            summary = summarize_intraday(
                snapshot, entry_price=entry_price,
                position_side=position.get("side", "long"),
            )
            review_id = f"REV-{uuid.uuid4().hex[:8].upper()}"
            self.brain.store.save_position_review(
                review_id=review_id,
                position_id=position["position_id"],
                current_price=current_price,
                pnl_pct=round(pnl_pct, 2),
                action=pre_llm_result["action"],
                reason=pre_llm_result.get("reason", ""),
                market_summary=summary,
                model="rule_engine",
                tokens_used=0,
            )
            self.brain.store.update_position_review(
                position["position_id"],
                action=pre_llm_result["action"],
                reason=pre_llm_result.get("reason", ""),
            )
            pre_llm_result["review_id"] = review_id
            pre_llm_result["position_id"] = position["position_id"]
            pre_llm_result["symbol"] = symbol
            pre_llm_result["current_price"] = current_price
            pre_llm_result["pnl_pct"] = round(pnl_pct, 2)
            return pre_llm_result

        # 2. Generate market summary
        summary = summarize_intraday(
            snapshot,
            entry_price=entry_price,
            position_side=position.get("side", "long"),
        )

        # 3. Get recent review history
        reviews = self.brain.store.list_position_reviews(
            position["position_id"], limit=3,
        )
        review_history = self._format_review_history(reviews)

        # 4. Build prompt
        force_review_section = ""
        if force_review_reason:
            force_review_section = FORCE_REVIEW_TEMPLATE.format(
                force_review_reason=force_review_reason,
            )

        user_msg = REVIEW_USER_TEMPLATE.format(
            symbol=symbol,
            name=name,
            side=position.get("side", "long"),
            entry_price=entry_price,
            qty=position.get("current_qty", 0),
            entry_date=position.get("entry_date", "unknown"),
            target_price=position.get("target_price", "未设"),
            stop_loss=position.get("stop_loss", "未设"),
            strategy=position.get("original_strategy", "未知"),
            thesis=position.get("original_thesis", "无"),
            bull_case=position.get("bull_case", "无"),
            bear_case=position.get("bear_case", "无"),
            force_review_section=force_review_section,
            review_history=review_history,
            market_summary=summary,
        )

        # 5. Call LLM
        llm = get_llm(model_name=self.model_name)
        messages = [
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        try:
            response = llm.invoke(messages)
            result = self._parse_response(response.content)
        except Exception as e:
            logger.error("LLM review failed for %s: %s", symbol, e)
            result = {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": f"LLM调用失败: {e}",
                "thesis_status": "unknown",
                "key_observation": "",
                "risk_flag": "LLM_ERROR",
            }

        # 6. Calculate P&L
        current_price = snapshot.current_price or entry_price
        pnl_pct = (current_price / entry_price - 1) * 100 if entry_price > 0 else 0
        if position.get("side") == "short":
            pnl_pct = -pnl_pct

        # 6.5 Rule Override: 止盈/止损硬约束（优先于 LLM 判断）
        result = self._apply_rule_override(result, position, current_price, pnl_pct)

        # 6.6 最低持仓期保护 — 短线持仓入场当天 LLM REDUCE 过早，降级为 HOLD
        # 仅对 LLM 主动 REDUCE 生效，不影响规则层触发的 EXIT/REDUCE（止损、目标价等）
        if (
            result["action"] == "REDUCE"
            and not result.get("rule_override")
            and position.get("entry_date")
        ):
            from datetime import date as date_cls
            days_held = (date_cls.today() - date_cls.fromisoformat(position["entry_date"])).days
            strategy = position.get("original_strategy", "")
            persona_id_val = position.get("persona_id", "")
            is_value = any(
                k in (persona_id_val or "") for k in ("duan", "buffett", "value")
            ) or any(
                k in (strategy or "") for k in ("价值投资", "护城河", "ROE", "分红")
            )
            min_hold = 2 if not is_value else 5
            if days_held < min_hold:
                logger.info(
                    "[%s] Min-hold gate: LLM REDUCE 被拦截 (持仓%d天 < %d天最低期)，降级为 HOLD",
                    symbol, days_held, min_hold,
                )
                result = {
                    **result,
                    "action": "HOLD",
                    "reason": f"持仓{days_held}天未达最低持仓期{min_hold}天，LLM减仓建议暂缓 (原因: {result.get('reason', '')})",
                    "risk_flag": "min_hold_gate",
                }

        # 7. Persist review
        review_id = f"REV-{uuid.uuid4().hex[:8].upper()}"
        tokens = getattr(response, "usage_metadata", {}).get("total_tokens", 0) if "response" in dir() else 0
        self.brain.store.save_position_review(
            review_id=review_id,
            position_id=position["position_id"],
            current_price=current_price,
            pnl_pct=round(pnl_pct, 2),
            action=result["action"],
            reason=result.get("reason", ""),
            market_summary=summary,
            model=self.model_name or "default",
            tokens_used=tokens,
        )
        self.brain.store.update_position_review(
            position["position_id"],
            action=result["action"],
            reason=result.get("reason", ""),
        )

        result["review_id"] = review_id
        result["position_id"] = position["position_id"]
        result["symbol"] = symbol
        result["current_price"] = current_price
        result["pnl_pct"] = round(pnl_pct, 2)
        return result

    def review_all_positions(self, force_review_map: dict[str, str] | None = None) -> list[dict]:
        """复审所有持仓，返回决策列表。

        Args:
            force_review_map: 可选的 {position_id: reason} 映射，对指定持仓注入强制复审上下文。

        说明：默认人格 (short_term_hot_rotation_v1) 会额外接管历史遗留的
        persona_id IS NULL 持仓，避免旧数据被三个人格集体遗忘（"买了不卖" bug）。
        """
        # 默认人格接管未指派持仓；其他人格严格按自身过滤
        include_unassigned = self.persona_id == "short_term_hot_rotation_v1"
        positions = self.brain.store.list_open_positions(
            persona_id=self.persona_id,
            include_unassigned=include_unassigned,
        )
        if not positions:
            logger.info("无持仓，跳过复审")
            return []

        force_map = force_review_map or {}
        logger.info(
            "开始复审 %d 只持仓 (persona=%s, include_unassigned=%s)",
            len(positions), self.persona_id, include_unassigned,
        )
        results = []
        for pos in positions:
            try:
                force_reason = force_map.get(pos["position_id"])
                result = self.review_position(pos, force_review_reason=force_reason)
                results.append(result)
                logger.info(
                    "[%s %s] %s (confidence=%.2f) — %s",
                    pos["symbol"], pos.get("name", ""),
                    result["action"], result.get("confidence", 0),
                    result.get("reason", ""),
                )
            except Exception as e:
                logger.error("复审 %s 异常: %s", pos["symbol"], e)
                results.append({
                    "action": "HOLD",
                    "position_id": pos["position_id"],
                    "symbol": pos["symbol"],
                    "reason": f"复审异常: {e}",
                    "risk_flag": "REVIEW_ERROR",
                })
        return results

    def _last_known_price(self, position: dict) -> float:
        """最近一次有效复审价（fallback: 入场价），用于异常价格检测。"""
        try:
            reviews = self.brain.store.list_position_reviews(
                position["position_id"], limit=1,
            )
            for r in reviews:
                price = float(r.get("current_price") or 0)
                if price > 0:
                    return price
        except Exception:  # noqa: BLE001
            pass
        return 0.0

    def _skip_review(
        self,
        position: dict,
        reason: str,
        reference_price: float,
        anomaly_price: float = 0.0,
    ) -> dict:
        """数据异常时安全跳过复审 — 记录 HOLD 审计，绝不触发交易。

        复审记录里的 current_price 写 reference（上次有效价），
        把异常值留在 reason 里，防止假价格污染后续 _last_known_price。
        """
        symbol = position["symbol"]
        entry_price = position.get("entry_price", 0) or 0
        pnl_pct = (reference_price / entry_price - 1) * 100 if entry_price > 0 else 0
        if position.get("side") == "short":
            pnl_pct = -pnl_pct

        full_reason = reason
        if anomaly_price > 0:
            full_reason = f"{reason}（异常价 {anomaly_price:.2f} 已忽略）"

        logger.warning(
            "[DATA-GUARD] %s %s 跳过复审: %s",
            symbol, position.get("name", ""), full_reason,
        )

        review_id = f"REV-{uuid.uuid4().hex[:8].upper()}"
        try:
            self.brain.store.save_position_review(
                review_id=review_id,
                position_id=position["position_id"],
                current_price=reference_price,
                pnl_pct=round(pnl_pct, 2),
                action="HOLD",
                reason=full_reason,
                market_summary=f"数据异常: {full_reason}",
                model="data_guard",
                tokens_used=0,
            )
            self.brain.store.update_position_review(
                position["position_id"], action="HOLD", reason=full_reason,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[DATA-GUARD] %s 复审记录保存失败: %s", symbol, e)

        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reason": full_reason,
            "thesis_status": "unknown",
            "key_observation": "",
            "risk_flag": "DATA_ANOMALY",
            "data_guard": True,
            "review_id": review_id,
            "position_id": position["position_id"],
            "symbol": symbol,
            "current_price": reference_price,
            "pnl_pct": round(pnl_pct, 2),
        }

    def _parse_response(self, content: str) -> dict:
        """Parse LLM JSON response, with fallback for malformed output."""
        text = content.strip()
        # Extract JSON from markdown code blocks
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("LLM output not valid JSON, extracting action keyword")
            action = "HOLD"
            for a in ("EXIT", "REDUCE", "ADD", "HOLD"):
                if a in content.upper():
                    action = a
                    break
            data = {
                "action": action,
                "confidence": 0.5,
                "reason": content[:200],
                "thesis_status": "unknown",
                "key_observation": "",
                "risk_flag": "PARSE_ERROR",
            }

        # Validate action
        valid_actions = {"HOLD", "ADD", "REDUCE", "EXIT"}
        if data.get("action", "").upper() not in valid_actions:
            data["action"] = "HOLD"
        else:
            data["action"] = data["action"].upper()

        return data

    def _pre_llm_hard_check(
        self, position: dict, current_price: float, pnl_pct: float,
    ) -> dict | None:
        """Pre-LLM 硬规则检查 — 与 hard_scan / 回测共用 evaluate_hard_exit。"""
        del pnl_pct  # 由 evaluate_hard_exit 内部重算，签名保持兼容
        return evaluate_hard_exit(position, current_price)

    def _apply_rule_override(
        self, result: dict, position: dict, current_price: float, pnl_pct: float,
    ) -> dict:
        """规则化止盈/止损覆盖 — 优先于 LLM 判断。

        规则:
        1. 止损: 当前价 <= stop_loss → EXIT
        2. 止盈(阶梯):
           - 当前价 >= target_price → REDUCE 一半 + 让剩余仓位跑（让利润奔跑）
           - 当前价 >= target_price * 0.95 → REDUCE 一半
        3. 浮亏超 -8% 且 LLM 仍 HOLD → 强制 EXIT
        4. 分级 held_too_long（保护赢家）:
           - 大赢家 pnl > 20% : 不受持仓天数限制（让趋势跑）
           - 常规赢家 pnl > 5% : 持仓 > 10 天 才 REDUCE
           - 平淡 -3 < pnl <= 5% : 持仓 > 5 天 REDUCE
           - 弱势 pnl <= -3% : 持仓 > 3 天 REDUCE
        """
        symbol = position.get("symbol", "")
        target_price = position.get("target_price") or 0
        stop_loss = position.get("stop_loss") or 0
        entry_date = position.get("entry_date", "")

        # 规则 1: 硬止损
        if stop_loss > 0 and current_price <= stop_loss:
            if result["action"] != "EXIT":
                logger.info(
                    "[%s] Rule Override: 跌破止损 %.2f (当前 %.2f) → EXIT",
                    symbol, stop_loss, current_price,
                )
                return {
                    **result,
                    "action": "EXIT",
                    "reason": f"价格{current_price:.2f}已跌破止损价{stop_loss:.2f}，强制清仓",
                    "risk_flag": "stop_loss_triggered",
                    "rule_override": True,
                }

        # 规则 2a: 到达目标价 → REDUCE 一半 (让利润奔跑，而不是全出)
        # 依据: 历史数据 held_too_long 平均 +554%，全出目标价会砍掉最大赢家
        if target_price > 0 and current_price >= target_price:
            if result["action"] in ("HOLD", "ADD"):
                logger.info(
                    "[%s] Rule Override: 达到目标价 %.2f (当前 %.2f) → REDUCE 50%% (保留一半让利润奔跑)",
                    symbol, target_price, current_price,
                )
                return {
                    **result,
                    "action": "REDUCE",
                    "reason": f"价格{current_price:.2f}已达目标价{target_price:.2f}，减仓50%锁利，剩余仓位继续持有",
                    "risk_flag": "",
                    "rule_override": True,
                }

        # 规则 2b: 接近目标价 (>=95%) → REDUCE 一半
        if target_price > 0 and current_price >= target_price * 0.95:
            if result["action"] in ("HOLD", "ADD"):
                logger.info(
                    "[%s] Rule Override: 接近目标价 %.2f (当前 %.2f, 95%%线=%.2f) → REDUCE",
                    symbol, target_price, current_price, target_price * 0.95,
                )
                return {
                    **result,
                    "action": "REDUCE",
                    "reason": f"价格{current_price:.2f}已接近目标价{target_price:.2f}(达95%)，减仓锁利",
                    "risk_flag": "",
                    "rule_override": True,
                }

        # 规则 3: 浮亏超 8% 且 LLM HOLD → 强制 EXIT
        if pnl_pct <= -8.0 and result["action"] == "HOLD":
            logger.info(
                "[%s] Rule Override: 浮亏 %.1f%% 超过阈值 → EXIT", symbol, pnl_pct,
            )
            return {
                **result,
                "action": "EXIT",
                "reason": f"浮亏{pnl_pct:.1f}%超过-8%硬止损线，强制清仓",
                "risk_flag": "max_loss_exceeded",
                "rule_override": True,
            }

        # 规则 3.5: 论点漂移检测 (AI Berkshire #3)
        # 如果 LLM 判断 thesis 已失效但仍建议 HOLD，强制 REDUCE/EXIT
        thesis_status = result.get("thesis_status", "intact")
        if thesis_status == "invalidated" and result["action"] in ("HOLD", "ADD"):
            # thesis 失效 + 浮亏 → EXIT; thesis 失效 + 浮盈 → REDUCE(锁利)
            if pnl_pct <= 0:
                logger.info(
                    "[%s] Thesis Drift: 论点失效+浮亏%.1f%% → EXIT", symbol, pnl_pct,
                )
                return {
                    **result,
                    "action": "EXIT",
                    "reason": f"买入论点已失效(thesis=invalidated)且浮亏{pnl_pct:.1f}%，清仓止损",
                    "risk_flag": "thesis_invalidated",
                    "rule_override": True,
                }
            else:
                logger.info(
                    "[%s] Thesis Drift: 论点失效+浮盈%.1f%% → REDUCE", symbol, pnl_pct,
                )
                return {
                    **result,
                    "action": "REDUCE",
                    "reason": f"买入论点已失效(thesis=invalidated)，浮盈{pnl_pct:.1f}%减仓锁利",
                    "risk_flag": "thesis_invalidated",
                    "rule_override": True,
                }
        elif thesis_status == "weakened" and result["action"] == "ADD":
            # thesis 弱化时禁止加仓
            logger.info(
                "[%s] Thesis Drift: 论点弱化(weakened)，拒绝加仓 → HOLD", symbol,
            )
            return {
                **result,
                "action": "HOLD",
                "reason": f"论点弱化(thesis=weakened)，不宜加仓，维持现有仓位观察",
                "risk_flag": "thesis_weakened",
                "rule_override": True,
            }

        # 规则 4: 分级 held_too_long（保护赢家）
        # 依据: 历史数据显示 6+ 天持仓平均 +201%，机械 5 天减仓正在砍最大赢家
        if entry_date and result["action"] == "HOLD":
            try:
                from datetime import date as date_cls
                days_held = (date_cls.today() - date_cls.fromisoformat(entry_date)).days

                # 大赢家: 不受持仓天数限制
                if pnl_pct > 20.0:
                    return result

                # 价值投资持仓: 使用长周期阈值（不适用短线规则）
                strategy = position.get("original_strategy", "")
                persona_id = position.get("persona_id", "")
                is_value_position = (
                    any(k in (persona_id or "") for k in ("duan", "buffett", "value"))
                    or any(k in (strategy or "") for k in ("价值投资", "护城河", "ROE", "分红"))
                )

                if is_value_position:
                    # 价值投资: 持仓 90 天内不触发超期
                    # 90天后浮亏才减仓（正常波动不干预）
                    if days_held <= 90:
                        return result
                    if pnl_pct > 0:
                        # 盈利中的价值持仓: 180天才考虑减仓
                        if days_held <= 180:
                            return result
                    # 90天+ 且浮亏: REDUCE
                    logger.info(
                        "[%s] Rule Override: 价值投资持仓 %d 天浮盈 %.1f%% → REDUCE",
                        symbol, days_held, pnl_pct,
                    )
                    return {
                        **result,
                        "action": "REDUCE",
                        "reason": f"价值投资持仓{days_held}天浮盈{pnl_pct:.1f}%（超90天阈值），减仓释放部分资金",
                        "risk_flag": "stale_position",
                        "rule_override": True,
                    }

                # 短线持仓: 使用原有分级阈值
                stale_threshold = None
                if pnl_pct > 5.0:
                    stale_threshold = 10
                elif pnl_pct > -3.0:  # 平淡区间
                    stale_threshold = 5
                else:  # -8% < pnl <= -3% 弱势
                    stale_threshold = 3

                if stale_threshold and days_held > stale_threshold:
                    logger.info(
                        "[%s] Rule Override: 持仓 %d 天浮盈 %.1f%% (阈值%d天) → REDUCE",
                        symbol, days_held, pnl_pct, stale_threshold,
                    )
                    return {
                        **result,
                        "action": "REDUCE",
                        "reason": f"持仓{days_held}天浮盈{pnl_pct:.1f}%（该盈利水平阈值{stale_threshold}天），减仓释放资金",
                        "risk_flag": "stale_position",
                        "rule_override": True,
                    }
            except (ValueError, TypeError):
                pass

        return result

    def _format_review_history(self, reviews: list[dict]) -> str:
        if not reviews:
            return "（首次复审，无历史记录）"
        lines = []
        for r in reviews:
            ts = r.get("review_at", "")[:16]
            lines.append(
                f"- {ts} | {r['action']} | 价格{r.get('current_price', '?')} | "
                f"盈亏{r.get('pnl_pct', '?')}% | {r.get('reason', '')[:60]}"
            )
        return "\n".join(lines)
