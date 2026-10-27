"""WheelReplay — 决策转盘历史回放与随机买点对照引擎（Phase 5，§10.3）。

职责：
1. 按交易日序列回放指定角色在 config/wheels.yaml 中绑定的日级/盘中事件；
2. 注入历史行情与历史时间（ctx.now = 历史时间戳），配合 PersonaProfile.as_of 杜绝未来函数；
3. 提供 random_entry_benchmark 随机买点对照闸门（策略每笔平均收益需优于同股票池随机买点分布）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import random
from typing import Any

from src.wheel.config import WheelConfig, get_config
from src.wheel.persona import persona_policy
from src.wheel.protocol import Decision, WheelEvent
from src.wheel.runner import run_binding


@dataclass(frozen=True)
class ReplayStep:
    """单次历史事件回放输入。"""

    now: datetime
    event_type: str
    subject: dict[str, Any]
    preloaded: dict[str, Any]


@dataclass(frozen=True)
class ReplayReport:
    """回放汇总报告。"""

    persona_id: str
    steps_run: int
    decisions: list[Decision]
    unreplayable_wheels: tuple[str, ...] = ("morning_watch",)


class WheelReplay:
    """同一份 config/wheels.yaml 的历史事件确定性回放器（Phase 5.1 / 5.2）。"""

    def __init__(self, brain: Any, cfg: WheelConfig | None = None) -> None:
        self.brain = brain
        self.cfg = cfg or get_config()

    def replay(
        self,
        persona_id: str,
        steps: list[ReplayStep],
        *,
        mode: str = "shadow",
    ) -> ReplayReport:
        from dataclasses import replace as dc_replace

        all_decisions: list[Decision] = []
        steps_run = 0
        for idx, step in enumerate(steps):
            binding = self.cfg.binding_for(persona_id, step.event_type)
            if not binding or binding.mode == "off":
                continue
            eff_binding = dc_replace(binding, mode=mode)
            pol = persona_policy(self.cfg, persona_id, eff_binding)
            ev = WheelEvent(
                event_id=f"replay-{persona_id}-{step.event_type}-{idx}-{step.now:%Y%m%d%H%M}",
                type=step.event_type,
                due_at=step.now,
                persona_id=persona_id,
                subject=dict(step.subject),
                source="wheel_replay",
            )
            results = run_binding(
                self.cfg,
                eff_binding,
                ev,
                pol,
                self.brain,
                preloaded=dict(step.preloaded),
                now=step.now,
            )
            steps_run += 1
            for r in results:
                all_decisions.extend(r.decisions)

        return ReplayReport(
            persona_id=persona_id,
            steps_run=steps_run,
            decisions=all_decisions,
        )


def random_entry_benchmark(
    strategy_returns_pct: list[float],
    universe_returns_pct: list[float],
    *,
    trials: int = 200,
    seed: int = 42,
) -> dict[str, Any]:
    """随机买点对照闸门（Phase 5.3，§10.3）：

    在相同股票池收益分布（universe_returns_pct）中随机抽取与策略交易笔数等量的样本，
    重复 trials 次，验证策略每笔平均收益是否显著优于随机买点均值。
    """
    if not strategy_returns_pct or not universe_returns_pct:
        return {
            "passed": False,
            "strategy_mean_pct": 0.0,
            "random_mean_pct": 0.0,
            "edge_pct": 0.0,
            "percentile": 0.0,
            "sample_size": len(strategy_returns_pct),
        }

    n = len(strategy_returns_pct)
    strat_mean = sum(strategy_returns_pct) / n
    rng = random.Random(seed)
    trial_means: list[float] = []
    for _ in range(max(1, trials)):
        sample = [rng.choice(universe_returns_pct) for _ in range(n)]
        trial_means.append(sum(sample) / n)

    rand_mean = sum(trial_means) / len(trial_means)
    beaten = sum(1 for m in trial_means if strat_mean > m)
    percentile = beaten / len(trial_means)
    edge = strat_mean - rand_mean

    return {
        "passed": strat_mean > rand_mean and percentile >= 0.60,
        "strategy_mean_pct": round(strat_mean, 3),
        "random_mean_pct": round(rand_mean, 3),
        "edge_pct": round(edge, 3),
        "percentile": round(percentile, 3),
        "sample_size": n,
    }
