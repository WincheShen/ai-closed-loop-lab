"""llm.* 与 rule.review_override — 复审类提议 Factor。

- llm.position_review：只消费 PositionReviewAgent 留档的 LLM *原始* 判断
  （llm_raw_action / llm_raw_thesis_status / llm_raw_reason），priority 固定
  >=500，只能在无规则提议时生效（advisory），并接受 mod.* 的 LLM 专属修饰
  （value_noise_filter / min_hold）。
- rule.review_override：_apply_rule_override / pre_llm_hard_check 产生的
  规则动作（rule_override=True）→ 按规则提议表达（priority 30），decided_by
  不以 llm. 开头，因此不会被 value_noise_filter / min_hold 误拦。
"""
from __future__ import annotations

from src.wheel.protocol import Context, Opinion

LLM_PRIORITY = 500


class LlmPositionReviewFactor:
    """把 PositionReviewAgent 的 LLM 原始判断映射为 propose Opinion。"""

    name = "llm.position_review"
    stage = "propose"
    requires = ("position", "llm_review")
    priority = LLM_PRIORITY
    param_keys: tuple = ()

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        review = ctx.data.get("llm_review") or {}
        overridden = bool(review.get("rule_override"))
        raw_action = review.get("llm_raw_action")
        if overridden and not raw_action:
            # pre-LLM 硬规则短路：LLM 根本没被调用，没有原始判断可表达
            return Opinion(
                factor=self.name, verdict="skip",
                reason="无 LLM 原始判断（pre-LLM 硬规则短路）",
            )
        # rule_override 时仍提议 LLM 原始动作 — 轨迹要记录"LLM 本来怎么想"；
        # 卖出类由 rule.review_override 以更高优先级胜出，HOLD 类覆盖
        # （T+1/价值降噪/论点减弱）由底线与 mod.* 兜底，结果与旧路径一致
        action = (raw_action or review.get("action") or "HOLD").upper()
        if action not in ("HOLD", "ADD", "REDUCE", "EXIT"):
            return Opinion(
                factor=self.name, verdict="skip",
                reason=f"LLM 动作非法: {action}",
            )
        return Opinion(
            factor=self.name,
            verdict="propose",
            action=action,
            priority=self.priority,
            reason=review.get("llm_raw_reason") or review.get("reason", ""),
            evidence={
                "confidence": review.get("confidence", 0),
                "thesis_status": (
                    review.get("llm_raw_thesis_status")
                    or review.get("thesis_status")
                    or "intact"
                ),
                "risk_flag": review.get("risk_flag", ""),
                "review_id": review.get("review_id"),
                "overridden": overridden,
            },
        )


class RuleReviewOverrideFactor:
    """复审中规则覆盖产生的卖出动作 → 规则提议（不是 LLM 噪音）。"""

    name = "rule.review_override"
    stage = "propose"
    requires = ("position", "llm_review")
    priority = 30
    param_keys: tuple = ()

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        review = ctx.data.get("llm_review") or {}
        if not review.get("rule_override"):
            return Opinion(factor=self.name, verdict="pass")
        action = (review.get("action") or "HOLD").upper()
        if action not in ("EXIT", "REDUCE"):
            return Opinion(factor=self.name, verdict="pass")
        return Opinion(
            factor=self.name,
            verdict="propose",
            action=action,
            priority=self.priority,
            reason=review.get("reason", ""),
            evidence={
                "risk_flag": review.get("risk_flag", ""),
                "thesis_status": review.get("thesis_status", "intact"),
                "rule_override": True,
                "review_id": review.get("review_id"),
                "price": float(review.get("current_price") or 0),
            },
        )


def register(reg) -> None:
    reg.register_factor(LlmPositionReviewFactor())
    reg.register_factor(RuleReviewOverrideFactor())
