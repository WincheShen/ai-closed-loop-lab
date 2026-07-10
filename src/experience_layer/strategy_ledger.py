"""StrategyLedger — 策略×regime 胜率台账。

替代 prompt_weights.json 的失效机制。
数据直接从 trade_attributions 表实时聚合，单一事实来源。
"""

from __future__ import annotations

from typing import Any

from src.central_brain import get_central_brain
from src.infra.logger import get_agent_logger

logger = get_agent_logger("experience", "strategy_ledger")

# 胜率低于此值且样本 >= MIN_SAMPLE 时，建议暂停
_SUSPEND_WIN_RATE = 0.30
# 胜率高于此值且样本 >= MIN_SAMPLE 时，建议加权
_BOOST_WIN_RATE = 0.60
# 最少样本数才出建议
_MIN_SAMPLE = 3
# 规则权重乘数上下界（防止极端漂移）
_WEIGHT_MULTIPLIER_MIN = 0.3
_WEIGHT_MULTIPLIER_MAX = 1.8


class StrategyLedger:
    """策略×regime 实时胜率台账。"""

    def __init__(self) -> None:
        self.brain = get_central_brain()

    def get_stats(
        self,
        strategy_id: str,
        regime: str | None = None,
    ) -> dict[str, Any] | None:
        """查询某策略在指定 regime 下的胜率统计。

        Returns:
            {
                "strategy_id": str,
                "regime": str | "all",
                "total_trades": int,
                "wins": int, "losses": int, "breakevens": int,
                "win_rate": float,
                "avg_pnl_pct": float,
                "recommendation": "BOOST" | "NORMAL" | "SUSPEND" | "INSUFFICIENT",
                "weight_multiplier": float,   # 给规则引擎用
            }
        """
        conn = self.brain.store._conn()
        if regime:
            rows = conn.execute(
                "SELECT outcome, pnl_pct FROM trade_attributions "
                "WHERE strategy_id = ? AND entry_regime = ?",
                (strategy_id, regime),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT outcome, pnl_pct FROM trade_attributions "
                "WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchall()

        if not rows:
            return None

        wins = sum(1 for r in rows if r["outcome"] == "win")
        losses = sum(1 for r in rows if r["outcome"] == "loss")
        breakevens = sum(1 for r in rows if r["outcome"] == "breakeven")
        total = len(rows)
        win_rate = wins / total if total > 0 else 0.0
        avg_pnl = sum(r["pnl_pct"] for r in rows) / total if total > 0 else 0.0

        # 推荐判定
        if total < _MIN_SAMPLE:
            recommendation = "INSUFFICIENT"
            multiplier = 1.0
        elif win_rate >= _BOOST_WIN_RATE:
            recommendation = "BOOST"
            multiplier = min(_WEIGHT_MULTIPLIER_MAX, 1.0 + (win_rate - 0.5))
        elif win_rate <= _SUSPEND_WIN_RATE:
            recommendation = "SUSPEND"
            multiplier = max(_WEIGHT_MULTIPLIER_MIN, win_rate + 0.1)
        else:
            recommendation = "NORMAL"
            multiplier = 1.0

        return {
            "strategy_id": strategy_id,
            "regime": regime or "all",
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "breakevens": breakevens,
            "win_rate": round(win_rate, 3),
            "avg_pnl_pct": round(avg_pnl, 2),
            "recommendation": recommendation,
            "weight_multiplier": round(multiplier, 2),
        }

    def get_all_stats(self, regime: str | None = None) -> list[dict[str, Any]]:
        """查询所有策略在指定 regime 下的统计。"""
        conn = self.brain.store._conn()
        if regime:
            rows = conn.execute(
                "SELECT DISTINCT strategy_id FROM trade_attributions "
                "WHERE entry_regime = ? AND strategy_id != ''",
                (regime,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT strategy_id FROM trade_attributions "
                "WHERE strategy_id != ''"
            ).fetchall()

        results = []
        for r in rows:
            stats = self.get_stats(r["strategy_id"], regime=regime)
            if stats:
                results.append(stats)

        return sorted(results, key=lambda x: x["total_trades"], reverse=True)

    def get_regime_matrix(self) -> dict[str, dict[str, dict]]:
        """生成策略×regime 的完整胜率矩阵。

        Returns:
            {
                "放量突破": {
                    "bull": {"wins": 2, "total": 3, "win_rate": 0.67},
                    "bear": {"wins": 0, "total": 3, "win_rate": 0.0},
                    ...
                },
                ...
            }
        """
        conn = self.brain.store._conn()
        rows = conn.execute(
            "SELECT strategy_id, entry_regime, outcome FROM trade_attributions "
            "WHERE strategy_id != '' AND entry_regime != ''"
        ).fetchall()

        matrix: dict[str, dict[str, dict]] = {}
        for r in rows:
            sid = r["strategy_id"]
            regime = r["entry_regime"]
            outcome = r["outcome"]

            if sid not in matrix:
                matrix[sid] = {}
            if regime not in matrix[sid]:
                matrix[sid][regime] = {"wins": 0, "losses": 0, "total": 0}

            cell = matrix[sid][regime]
            cell["total"] += 1
            if outcome == "win":
                cell["wins"] += 1
            elif outcome == "loss":
                cell["losses"] += 1

        # 计算胜率
        for sid in matrix:
            for regime in matrix[sid]:
                cell = matrix[sid][regime]
                cell["win_rate"] = round(
                    cell["wins"] / cell["total"] if cell["total"] > 0 else 0, 3
                )

        return matrix

    def generate_prompt_block(self, regime: str | None = None) -> str:
        """生成注入 Strategist prompt 的策略表现总结。

        替代旧的 PromptEvolution.generate_evolution_prompt()。
        基于 DB 实时数据，不依赖 JSON 文件。
        """
        stats = self.get_all_stats(regime=regime)
        if not stats:
            return ""

        lines = ["\n## 策略实盘表现（基于历史交易归因）"]
        if regime:
            lines[0] += f"（当前环境: {regime}）"

        for s in stats:
            total = s["total_trades"]
            wr = s["win_rate"]
            avg = s["avg_pnl_pct"]
            rec = s["recommendation"]

            if rec == "BOOST":
                icon = "[强]"
            elif rec == "SUSPEND":
                icon = "[弱]"
            elif rec == "INSUFFICIENT":
                icon = "[?]"
            else:
                icon = "[中]"

            line = (
                f"  - {icon} {s['strategy_id']}: "
                f"{s['wins']}胜/{s['losses']}负 "
                f"胜率{wr:.0%} 均盈亏{avg:+.1f}%"
            )
            if total < _MIN_SAMPLE:
                line += f" (仅{total}次样本)"
            lines.append(line)

        lines.append(
            "\n  提示: [强]=近期表现优异可优先, "
            "[弱]=近期表现差应谨慎, [?]=样本不足暂不判断"
        )
        return "\n".join(lines)
