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

## 历史复审记录
{review_history}

## 最新盘中走势
{market_summary}

请根据以上信息给出你的复审判断。
"""


class PositionReviewAgent:
    """轻量级 LLM 持仓复审 Agent。"""

    def __init__(self, model_name: str | None = None) -> None:
        self.brain = get_central_brain()
        self.intraday = IntradayClient(allow_mock_fallback=True)
        self.model_name = model_name

    def review_position(self, position: dict) -> dict:
        """复审单只持仓，返回决策结果。

        Args:
            position: 从 store.list_open_positions() 获取的持仓 dict

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

    def review_all_positions(self) -> list[dict]:
        """复审所有持仓，返回决策列表。"""
        positions = self.brain.store.list_open_positions()
        if not positions:
            logger.info("无持仓，跳过复审")
            return []

        logger.info("开始复审 %d 只持仓", len(positions))
        results = []
        for pos in positions:
            try:
                result = self.review_position(pos)
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
