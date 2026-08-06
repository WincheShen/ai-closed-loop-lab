"""Kelly Criterion 动态仓位计算器。

根据策略的历史胜率和盈亏比，使用 Kelly 公式计算最优仓位。
采用 Half-Kelly（保守版）避免因样本不足导致过度下注。

Kelly 公式:
    f* = (p * b - q) / b
    
    p = 胜率 (win_rate)
    q = 败率 (1 - p)
    b = 赔率 = avg_win / avg_loss (盈亏比)

Half-Kelly:
    f_actual = 0.5 * f*

安全约束:
    - 最小样本数: 不足时退回默认仓位
    - 上下限: [min_fraction, max_fraction]
    - regime 调整: 熊市/恐慌时额外折扣
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.central_brain import get_central_brain
from src.infra.logger import get_agent_logger

logger = get_agent_logger("kelly_sizer", "init")

# 安全约束
MIN_SAMPLE_FOR_KELLY = 8            # 至少 8 笔交易才启用 Kelly
MIN_FRACTION = 0.02                  # 最小仓位 2%
MAX_FRACTION_SHORT = 0.10            # 短线最大仓位 10%
MAX_FRACTION_VALUE = 0.25            # 价值投资最大仓位 25%
DEFAULT_FRACTION_SHORT = 0.05        # 短线默认仓位 5%（样本不足时）
DEFAULT_FRACTION_VALUE = 0.10        # 价值投资默认仓位 10%

# regime 折扣系数
REGIME_DISCOUNT = {
    "bull": 1.0,        # 牛市不折扣
    "neutral": 0.85,    # 中性环境打 85 折
    "bear": 0.6,        # 熊市打 6 折
    "panic": 0.3,       # 恐慌打 3 折
    "defend": 0.5,      # 防御打 5 折
}


@dataclass
class KellyResult:
    """Kelly 计算结果。"""
    raw_kelly: float           # 原始 Kelly fraction
    half_kelly: float          # Half-Kelly
    regime_adjusted: float     # regime 折扣后
    final_fraction: float      # 经安全约束后的最终仓位
    method: str                # "kelly" / "default" / "min_sample"
    stats: dict                # 用于计算的统计数据


class KellySizer:
    """基于 Kelly Criterion 的动态仓位计算器。"""

    def __init__(self) -> None:
        self.brain = get_central_brain()

    def calculate(
        self,
        strategy_id: str,
        regime: str | None = None,
        is_value: bool = False,
    ) -> KellyResult:
        """计算策略在当前 regime 下的 Kelly 仓位。

        Args:
            strategy_id: 策略标识（如 "热点板块前排回踩"）
            regime: 当前市场环境
            is_value: 是否价值投资策略

        Returns:
            KellyResult 包含最终仓位比例
        """
        max_frac = MAX_FRACTION_VALUE if is_value else MAX_FRACTION_SHORT
        default_frac = DEFAULT_FRACTION_VALUE if is_value else DEFAULT_FRACTION_SHORT

        # 1. 从 trade_attributions 获取策略历史数据
        stats = self._get_strategy_pnl_stats(strategy_id, regime)

        if not stats or stats["total_trades"] < MIN_SAMPLE_FOR_KELLY:
            sample = stats["total_trades"] if stats else 0
            logger.debug(
                "Kelly: %s 样本不足 (%d < %d)，使用默认仓位 %.0f%%",
                strategy_id, sample, MIN_SAMPLE_FOR_KELLY, default_frac * 100,
            )
            return KellyResult(
                raw_kelly=0,
                half_kelly=0,
                regime_adjusted=0,
                final_fraction=default_frac,
                method="min_sample",
                stats=stats or {},
            )

        # 2. 计算 Kelly
        win_rate = stats["win_rate"]
        avg_win = stats["avg_win_pct"]
        avg_loss = abs(stats["avg_loss_pct"])  # 取绝对值

        if avg_loss <= 0 or win_rate <= 0:
            return KellyResult(
                raw_kelly=0, half_kelly=0, regime_adjusted=0,
                final_fraction=default_frac, method="default", stats=stats,
            )

        # Kelly formula: f* = (p*b - q) / b
        b = avg_win / avg_loss  # 赔率（盈亏比）
        q = 1 - win_rate
        raw_kelly = (win_rate * b - q) / b

        # 3. Half-Kelly（保守）
        half_kelly = 0.5 * raw_kelly

        # 4. Regime 折扣
        discount = REGIME_DISCOUNT.get(regime or "neutral", 0.85)
        regime_adjusted = half_kelly * discount

        # 5. 安全约束
        if regime_adjusted <= 0:
            # 负 Kelly = 不应该交易（但由 RiskGovernor 决定是否放行）
            final = MIN_FRACTION
            method = "kelly_negative"
        else:
            final = max(MIN_FRACTION, min(max_frac, regime_adjusted))
            method = "kelly"

        logger.info(
            "Kelly: %s | WR=%.0f%% b=%.2f | raw=%.1f%% half=%.1f%% "
            "regime(%s)=%.1f%% → final=%.1f%%",
            strategy_id, win_rate * 100, b,
            raw_kelly * 100, half_kelly * 100,
            regime or "?", regime_adjusted * 100, final * 100,
        )

        return KellyResult(
            raw_kelly=round(raw_kelly, 4),
            half_kelly=round(half_kelly, 4),
            regime_adjusted=round(regime_adjusted, 4),
            final_fraction=round(final, 4),
            method=method,
            stats=stats,
        )

    def _get_strategy_pnl_stats(
        self, strategy_id: str, regime: str | None = None,
    ) -> dict[str, Any] | None:
        """从 trade_attributions 获取策略的详细盈亏统计。"""
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

        wins = [r for r in rows if r["outcome"] == "win"]
        losses = [r for r in rows if r["outcome"] == "loss"]
        total = len(rows)

        win_rate = len(wins) / total if total > 0 else 0
        avg_win_pct = (
            sum(r["pnl_pct"] for r in wins) / len(wins) if wins else 0
        )
        avg_loss_pct = (
            sum(r["pnl_pct"] for r in losses) / len(losses) if losses else 0
        )

        return {
            "strategy_id": strategy_id,
            "regime": regime or "all",
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 3),
            "avg_win_pct": round(avg_win_pct, 2),
            "avg_loss_pct": round(avg_loss_pct, 2),
            "profit_factor": round(
                abs(avg_win_pct * len(wins)) / abs(avg_loss_pct * len(losses))
                if losses and avg_loss_pct != 0 else 0,
                2,
            ),
        }

    def get_all_kelly_fractions(self, regime: str | None = None) -> list[dict]:
        """获取所有策略的 Kelly 仓位建议。"""
        conn = self.brain.store._conn()
        strategies = conn.execute(
            "SELECT DISTINCT strategy_id FROM trade_attributions "
            "WHERE strategy_id != ''"
        ).fetchall()

        results = []
        for row in strategies:
            sid = row["strategy_id"]
            result = self.calculate(sid, regime=regime)
            results.append({
                "strategy_id": sid,
                "kelly_fraction": result.final_fraction,
                "method": result.method,
                "win_rate": result.stats.get("win_rate", 0),
                "trades": result.stats.get("total_trades", 0),
            })

        return sorted(results, key=lambda x: x["kelly_fraction"], reverse=True)


# Singleton
_kelly_sizer: KellySizer | None = None


def get_kelly_sizer() -> KellySizer:
    """获取全局 KellySizer 实例。"""
    global _kelly_sizer
    if _kelly_sizer is None:
        _kelly_sizer = KellySizer()
    return _kelly_sizer
