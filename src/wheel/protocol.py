"""决策转盘协议 — 所有步骤间通信的唯一数据契约。

设计约定（见 docs/decision_wheel_architecture.md §3/§5）：
- 转盘必须是确定性的：同一事件 + 同一数据库快照 → 同一决策。
- Factor 是纯函数：只读 Context，不发网络请求，不直接写库；
  需要持久状态时通过 Opinion.state_update / clear_state 返回，由 runner 统一落库。
- LLM 产出的"原始判断"由 DataStep 采集（llm_review），llm.* Factor 只做
  结构化映射，priority 固定 >= 500，只能在无规则提议时生效。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Literal, Protocol

Action = Literal["BUY", "HOLD", "ADD", "REDUCE", "EXIT", "WATCH"]
Verdict = Literal["pass", "veto", "propose", "modify", "skip"]
Stage = Literal["veto", "propose", "modify", "screen"]

# 动作强度 — modify 只允许把动作调"弱"（数值变小），不允许调强。
# EXIT 最强（全卖），其次 REDUCE/ADD/BUY，HOLD/WATCH 最弱。
ACTION_STRENGTH: dict[str, int] = {
    "EXIT": 4,
    "REDUCE": 3,
    "ADD": 2,
    "BUY": 2,
    "HOLD": 1,
    "WATCH": 1,
}


def is_weaker(new_action: str | None, old_action: str | None) -> bool:
    """modify 合法性检查：新动作不得强于旧动作。"""
    return ACTION_STRENGTH.get(new_action or "HOLD", 1) <= ACTION_STRENGTH.get(
        old_action or "HOLD", 1,
    )


@dataclass(frozen=True)
class WheelEvent:
    """进入转盘的事件。事件定义/调度不属于转盘（见 §3）。"""

    event_id: str                     # 幂等键
    type: str                         # clock.0935 / followup.* / market.* / position.* / account.*
    due_at: datetime
    persona_id: str | None = None     # None = 对所有绑定该事件的角色广播
    subject: dict = field(default_factory=dict)   # {"position_id": ...} / {"symbol": ...}
    payload: dict = field(default_factory=dict)
    source: str = "scheduler"         # scheduler / wheel:<name> / drift_detector / account_monitor
    parent_event_id: str | None = None
    attempt: int = 1
    max_attempts: int = 1
    expires_at: datetime | None = None


@dataclass(frozen=True)
class FollowUp:
    """转盘运行时产生的后续观察事件（角色自定义观察，见 §3.3）。"""

    type: str
    after: timedelta
    subject: dict
    payload: dict = field(default_factory=dict)
    max_attempts: int = 1
    until: time | None = None


@dataclass(frozen=True)
class Opinion:
    """一个判断依据（Factor）的结构化输出。"""

    factor: str
    verdict: Verdict
    action: Action | None = None
    qty_pct: float | None = None
    priority: int = 100                 # 越小越优先，仅 propose 使用；LLM 固定 >= 500
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    state_update: dict | None = None    # runner 合并写入 wheel_factor_state
    clear_state: bool = False           # 清除该 Factor 对 subject 的历史状态
    follow_up: FollowUp | None = None


@dataclass(frozen=True)
class Decision:
    """转盘对一个 subject 的最终裁决。"""

    wheel: str
    subject_key: str                    # position_id / symbol / account
    action: str                         # HOLD/EXIT/REDUCE/ADD/BUY/WATCH/SKIP
    qty_pct: float | None = None
    reason: str = ""
    decided_by: str = ""                # 做出结论的 Factor（或 floor / none）
    risk_flag: str = ""
    price: float = 0.0
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PersonaPolicy:
    """角色行为配置快照（转盘外装配，只读）。身份层见 PersonaProfile（Phase 3.6）。"""

    persona_id: str
    kind: str = "short"                 # short | ultra | value
    params: dict = field(default_factory=dict)   # 当前绑定携带的转盘参数
    allowed_strategies: tuple[str, ...] = ()
    llm_review: bool = True


@dataclass
class Context:
    """一次转盘运行的共享上下文。Factor 只读它，不互相调用。"""

    event: WheelEvent
    persona: PersonaPolicy
    now: datetime                       # 回测时为模拟时间
    brain: Any
    data: dict[str, Any] = field(default_factory=dict)   # DataStep 产出
    state: dict[str, dict] = field(default_factory=dict)  # factor -> 持久状态
    mode: str = "shadow"                # shadow | live
    resolved: Decision | None = None    # 裁决段产出，modify 阶段读取


@dataclass
class WheelResult:
    wheel: str
    decisions: list[Decision] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    follow_ups: list[FollowUp] = field(default_factory=list)
    trace: list[Opinion] = field(default_factory=list)


class DataStep(Protocol):
    """取数步骤：把外部数据搬进 Context.data。可发网络请求、可读库。"""

    name: str
    provides: tuple[str, ...]

    def load(self, ctx: Context, params: dict) -> dict: ...


class Factor(Protocol):
    """判断步骤：纯函数。只读 Context，输出 Opinion。"""

    name: str
    stage: Stage
    requires: tuple[str, ...]           # 依赖的 Context.data 键，缺失则 skip

    def evaluate(self, ctx: Context, params: dict) -> Opinion: ...
