"""StockMemory — 个股交易记忆。

记录每只股票的交易历史，提供：
- 交易次数、胜率、平均持仓天数
- 最近一次交易时间和结果
- 按策略拆分的胜率
- 冷却期判断（连续止损后自动冷却）
- 相关 lessons

所有数据从 trade_attributions + lessons 表实时聚合。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from src.central_brain import get_central_brain
from src.infra.logger import get_agent_logger

logger = get_agent_logger("experience", "stock_memory")

# 连续止损次数阈值 → 触发冷却
_CONSECUTIVE_LOSS_COOLDOWN = 2
# 冷却天数
_COOLDOWN_DAYS = 5


class StockMemory:
    """个股交易记忆 — 从 trade_attributions 实时聚合。"""

    def __init__(self) -> None:
        self.brain = get_central_brain()

    def get_history(self, symbol: str) -> dict[str, Any] | None:
        """查询某只股票的完整交易记忆。

        Returns:
            {
                "symbol": str,
                "name": str,
                "trade_count": int,
                "win_rate": float,
                "avg_pnl_pct": float,
                "avg_holding_days": float,
                "last_traded": str,          # ISO date
                "last_outcome": str,         # win/loss/breakeven
                "strategies_used": {          # 按策略拆分
                    "热点板块前排回踩": {"wins": 2, "losses": 0, "total": 2},
                    ...
                },
                "lessons": [str, ...],       # 最近3条
                "consecutive_losses": int,   # 当前连续止损次数
                "cooldown_until": date|None, # 冷却截止日
            }
        """
        conn = self.brain.store._conn()
        rows = conn.execute(
            "SELECT * FROM trade_attributions WHERE symbol = ? "
            "ORDER BY created_at DESC",
            (symbol,),
        ).fetchall()

        if not rows:
            return None

        rows = [dict(r) for r in rows]
        total = len(rows)
        wins = sum(1 for r in rows if r["outcome"] == "win")
        losses = sum(1 for r in rows if r["outcome"] == "loss")
        avg_pnl = sum(r.get("pnl_pct", 0) or 0 for r in rows) / total
        avg_hold = sum(r.get("holding_days", 0) or 0 for r in rows) / total

        # 按策略拆分
        strategies: dict[str, dict] = {}
        for r in rows:
            sid = r.get("strategy_id", "unknown")
            if sid not in strategies:
                strategies[sid] = {"wins": 0, "losses": 0, "total": 0}
            strategies[sid]["total"] += 1
            if r["outcome"] == "win":
                strategies[sid]["wins"] += 1
            elif r["outcome"] == "loss":
                strategies[sid]["losses"] += 1

        # 连续止损次数（从最近开始数）
        consecutive_losses = 0
        for r in rows:
            if r["outcome"] == "loss":
                consecutive_losses += 1
            else:
                break

        # 冷却期计算
        cooldown_until = None
        if consecutive_losses >= _CONSECUTIVE_LOSS_COOLDOWN:
            last_loss_date_str = rows[0].get("created_at", "")
            try:
                last_loss_date = datetime.fromisoformat(last_loss_date_str[:10])
                cooldown_until = (last_loss_date + timedelta(days=_COOLDOWN_DAYS)).date()
            except (ValueError, TypeError):
                pass

        # 获取相关 lessons
        lesson_rows = conn.execute(
            "SELECT lesson_text FROM lessons WHERE symbol = ? "
            "ORDER BY created_at DESC LIMIT 3",
            (symbol,),
        ).fetchall()
        lessons = [r["lesson_text"] for r in lesson_rows if r["lesson_text"]]

        return {
            "symbol": symbol,
            "name": rows[0].get("name", ""),
            "trade_count": total,
            "win_rate": round(wins / total, 3) if total > 0 else 0,
            "avg_pnl_pct": round(avg_pnl, 2),
            "avg_holding_days": round(avg_hold, 1),
            "last_traded": rows[0].get("created_at", "")[:10],
            "last_outcome": rows[0].get("outcome", ""),
            "strategies_used": strategies,
            "lessons": lessons,
            "consecutive_losses": consecutive_losses,
            "cooldown_until": cooldown_until,
        }

    def is_in_cooldown(self, symbol: str) -> bool:
        """判断某只股票是否在冷却期内。"""
        history = self.get_history(symbol)
        if not history or not history.get("cooldown_until"):
            return False
        return datetime.now().date() <= history["cooldown_until"]

    def get_cooldown_reason(self, symbol: str) -> str | None:
        """返回冷却原因描述，None 表示不在冷却期。"""
        history = self.get_history(symbol)
        if not history or not history.get("cooldown_until"):
            return None
        if datetime.now().date() > history["cooldown_until"]:
            return None
        return (
            f"{symbol} {history['name']} 连续{history['consecutive_losses']}次止损，"
            f"冷却至 {history['cooldown_until']}"
        )

    def generate_prompt_block(self, symbol: str) -> str:
        """为 Strategist 生成个股历史记忆的 prompt 片段。"""
        history = self.get_history(symbol)
        if not history or history["trade_count"] == 0:
            return ""

        h = history
        lines = [
            f"\n## 该股历史交易记录（{h['name']}）",
            f"  - 共交易 {h['trade_count']} 次，胜率 {h['win_rate']:.0%}，"
            f"均盈亏 {h['avg_pnl_pct']:+.1f}%",
            f"  - 平均持仓 {h['avg_holding_days']:.0f} 天",
            f"  - 上次交易: {h['last_traded']}，结果: {h['last_outcome']}",
        ]

        # 按策略拆分
        for sid, stats in h["strategies_used"].items():
            lines.append(
                f"  - 策略「{sid}」: {stats['wins']}胜/{stats['losses']}负"
            )

        # 相关教训
        if h["lessons"]:
            lines.append("  - 相关教训:")
            for lesson in h["lessons"]:
                lines.append(f"    * {lesson}")

        if h.get("cooldown_until"):
            lines.append(
                f"  - !! 该股连续{h['consecutive_losses']}次止损，"
                f"冷却至 {h['cooldown_until']} !!"
            )

        return "\n".join(lines)
