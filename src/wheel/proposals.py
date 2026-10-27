"""persona_proposals 复盘提议与审批流转通道（Phase 4b，§7.6）。

原则：
- 经验/教训自动进记忆；行为配置（wheels.yaml 参数/绑定/策略开关）只能生成提议。
- 样本不足（< min_sample_trades）自动降级为 status='observe'，不进入审批。
- 只有经回测闸门与人工审批（status='approved'）后才可导出变更，严禁未经审批自动回写配置文件。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

MIN_SAMPLE_TRADES = 20
_VALID_KINDS = ("param_change", "binding_change", "strategy_toggle", "setup_preference")
_VALID_STATUSES = ("proposed", "observe", "backtested", "approved", "rejected")


def submit_proposal(
    store,
    *,
    persona_id: str,
    kind: str,
    change: dict[str, Any],
    rationale: str,
    evidence: dict[str, Any] | None = None,
    sample_size: int | None = None,
    min_sample: int = MIN_SAMPLE_TRADES,
) -> dict[str, Any]:
    """提交一条角色配置变更提议。样本不足时自动标记为 observe。"""
    if kind not in _VALID_KINDS:
        raise ValueError(f"非法提议类型: {kind!r}（允许: {_VALID_KINDS}）")

    ev = dict(evidence or {})
    trades_n = int(
        sample_size
        if sample_size is not None
        else ev.get("trades_count", ev.get("sample_size", 0))
    )
    status = "observe" if trades_n < min_sample else "proposed"
    proposal_id = f"PRP-{uuid.uuid4().hex[:10].upper()}"
    now_iso = datetime.now().isoformat()

    record = {
        "proposal_id": proposal_id,
        "persona_id": persona_id,
        "kind": kind,
        "change": change,
        "rationale": rationale,
        "evidence": {**ev, "sample_size": trades_n},
        "status": status,
        "created_at": now_iso,
    }
    store.save_persona_proposal(record)
    logger.info(
        "[Proposal] 已创建提议 %s (persona=%s kind=%s status=%s sample=%d)",
        proposal_id, persona_id, kind, status, trades_n,
    )
    return record
