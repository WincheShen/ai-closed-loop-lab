"""llm.* — LLM 提议 Factor。

LLM 只能"提议"：priority 固定 >= 500，只能在无规则提议时生效（advisory）。
原始 LLM 判断由 data.llm_review 采集；本 Factor 只做结构化映射，是纯函数。
"""
from __future__ import annotations

from src.wheel.protocol import Context, Opinion

LLM_PRIORITY = 500


class LlmPositionReviewFactor:
    """把 PositionReviewAgent 的复审结果映射为 propose Opinion。"""

    name = "llm.position_review"
    stage = "propose"
    requires = ("position", "llm_review")
    priority = LLM_PRIORITY

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        review = ctx.data.get("llm_review") or {}
        action = (review.get("action") or "HOLD").upper()
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
            reason=review.get("reason", ""),
            evidence={
                "confidence": review.get("confidence", 0),
                "thesis_status": review.get("thesis_status", "intact"),
                "risk_flag": review.get("risk_flag", ""),
                "review_id": review.get("review_id"),
                "rule_override": bool(review.get("rule_override")),
            },
        )


def register(reg) -> None:
    reg.register_factor(LlmPositionReviewFactor())
