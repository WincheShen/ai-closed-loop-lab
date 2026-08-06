"""盘中 Regime Drift 检测 — 5分钟级别快速反应。

与 MarketBrain.generate_snapshot() 的区别:
- 不调用 LLM（纯量化规则，<1s 完成）
- 不刷新热点板块（开销太大）
- 仅与当前存储的 regime 做对比，检测是否发生漂移
- 漂移时触发告警 + 可选触发盘中紧急复审

设计:
    scheduler 每 5 分钟调用 → detect_regime_drift()
    - 无漂移: 仅记录日志
    - 轻度漂移 (neutral→bear): 记录警告
    - 重度漂移 (bull→panic): 触发紧急复审 + 调整 max_position_pct
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.central_brain import get_central_brain
from src.infra.logger import get_agent_logger
from src.stock_analyzer.data_source import AkshareClient

logger = get_agent_logger("regime_drift", "init")

# 漂移严重度矩阵 (from_regime → to_regime → severity)
# severity: 0=无漂移, 1=轻度, 2=中度, 3=重度
_DRIFT_SEVERITY = {
    "bull":    {"bull": 0, "rebound": 1, "neutral": 2, "bear": 3, "panic": 3},
    "rebound": {"bull": 0, "rebound": 0, "neutral": 1, "bear": 2, "panic": 3},
    "neutral": {"bull": 1, "rebound": 0, "neutral": 0, "bear": 2, "panic": 3},
    "bear":    {"bull": 2, "rebound": 1, "neutral": 1, "bear": 0, "panic": 2},
    "panic":   {"bull": 3, "rebound": 2, "neutral": 2, "bear": 1, "panic": 0},
}

# 重度漂移时 max_position_pct 应急调整
_EMERGENCY_MAX_POSITION = {
    "panic": 0.0,       # 恐慌模式：清仓/不加仓
    "bear": 0.30,       # 熊市：最多 30%
    "neutral": 0.60,    # 中性：最多 60%
    "rebound": 0.80,    # 反弹：最多 80%
    "bull": 1.0,        # 牛市：全仓可
}


class RegimeDriftDetector:
    """盘中 5 分钟快速 regime drift 检测。"""

    def __init__(self) -> None:
        self.brain = get_central_brain()
        self.akshare = AkshareClient(allow_mock_fallback=True)
        self._last_computed_regime: str | None = None
        self._drift_count = 0        # 连续漂移次数（防止单次噪音）
        self._confirm_threshold = 2  # 连续 N 次确认才触发

    def detect(self) -> dict[str, Any]:
        """执行一次 drift 检测。

        Returns:
            {
                "drifted": bool,
                "severity": int (0-3),
                "from_regime": str,
                "to_regime": str,
                "evidence": dict,
                "action_taken": str | None,
            }
        """
        # 1. 获取当前存储的 regime
        current_stored = self.brain.store.latest_market_regime()
        stored_regime = current_stored.get("regime", "neutral") if current_stored else "neutral"

        # 2. 快速抓取实时行情
        try:
            snapshot = self.akshare.fetch_snapshot()
        except Exception as e:
            logger.warning("Drift 检测: 行情获取失败: %s", e)
            return {"drifted": False, "severity": 0, "error": str(e)}

        if snapshot.is_mock:
            # 模拟数据不做漂移判断
            return {"drifted": False, "severity": 0, "note": "mock_data"}

        # 3. 纯量化 regime 计算（与 MarketBrain._compute_base_regime 相同逻辑）
        computed_regime, evidence = self._compute_quick_regime(snapshot)
        self._last_computed_regime = computed_regime

        # 4. 判断漂移
        severity = _DRIFT_SEVERITY.get(stored_regime, {}).get(computed_regime, 0)

        if severity == 0:
            self._drift_count = 0
            return {
                "drifted": False,
                "severity": 0,
                "from_regime": stored_regime,
                "to_regime": computed_regime,
                "evidence": evidence,
            }

        # 5. 连续确认机制（避免单次噪音）
        self._drift_count += 1
        confirmed = self._drift_count >= self._confirm_threshold

        if not confirmed:
            logger.info(
                "Drift 信号 %d/%d: %s → %s (severity=%d, 待确认)",
                self._drift_count, self._confirm_threshold,
                stored_regime, computed_regime, severity,
            )
            return {
                "drifted": False,
                "severity": severity,
                "from_regime": stored_regime,
                "to_regime": computed_regime,
                "evidence": evidence,
                "note": f"confirming ({self._drift_count}/{self._confirm_threshold})",
            }

        # 6. 确认漂移！执行响应动作
        self._drift_count = 0
        action = self._respond_to_drift(stored_regime, computed_regime, severity, evidence)

        logger.warning(
            "⚠️ REGIME DRIFT 确认! %s → %s (severity=%d) | action=%s",
            stored_regime, computed_regime, severity, action,
        )

        return {
            "drifted": True,
            "severity": severity,
            "from_regime": stored_regime,
            "to_regime": computed_regime,
            "evidence": evidence,
            "action_taken": action,
        }

    def _compute_quick_regime(self, snapshot) -> tuple[str, dict]:
        """轻量级 regime 计算（与 MarketBrain 相同规则）。"""
        stocks = snapshot.stocks or []
        if not stocks:
            return "neutral", {"error": "no_stocks"}

        up = sum(1 for s in stocks if s.change_pct > 0)
        down = sum(1 for s in stocks if s.change_pct < 0)
        strong = sum(1 for s in stocks if s.change_pct >= 7.0)
        weak = sum(1 for s in stocks if s.change_pct <= -7.0)
        avg_change = sum(s.change_pct for s in stocks) / len(stocks)
        up_ratio = up / max(len(stocks), 1)

        # 与 MarketBrain._compute_base_regime 完全一致的规则
        if avg_change >= 1.5 and strong >= 50 and weak < 30:
            regime = "bull"
        elif avg_change <= -1.5 and weak >= 80:
            regime = "panic" if weak >= 200 else "bear"
        elif avg_change >= 0.8 and up_ratio >= 0.55:
            regime = "rebound"
        elif avg_change <= -0.8 or up_ratio <= 0.35:
            regime = "bear"
        else:
            regime = "neutral"

        evidence = {
            "up_count": up,
            "down_count": down,
            "strong_count": strong,
            "weak_count": weak,
            "avg_change_pct": round(avg_change, 2),
            "up_ratio": round(up_ratio, 3),
            "total_stocks": len(stocks),
            "computed_at": datetime.now().isoformat(),
        }
        return regime, evidence

    def _respond_to_drift(
        self,
        from_regime: str,
        to_regime: str,
        severity: int,
        evidence: dict,
    ) -> str:
        """对确认的漂移执行响应动作。"""
        actions = []

        # 写入告警事件
        self.brain.log_agent_event(
            session_id=f"drift-{datetime.now().strftime('%Y%m%d-%H%M')}",
            agent="regime_drift_detector",
            event_type="regime_drift_detected",
            payload={
                "from_regime": from_regime,
                "to_regime": to_regime,
                "severity": severity,
                "evidence": evidence,
            },
        )
        actions.append("logged_alert")

        # 重度漂移 (severity >= 2): 更新 regime snapshot
        if severity >= 2:
            self._update_regime_in_store(to_regime, evidence)
            actions.append("updated_regime")

        # 向恶化方向的重度漂移: 触发紧急复审
        worse_direction = (
            (from_regime in ("bull", "rebound", "neutral") and to_regime in ("bear", "panic"))
        )
        if severity >= 2 and worse_direction:
            self._trigger_emergency_review(to_regime)
            actions.append("triggered_emergency_review")

        return ", ".join(actions)

    def _update_regime_in_store(self, new_regime: str, evidence: dict) -> None:
        """更新存储的 regime（drift 触发的中间更新）。"""
        import uuid

        max_pos = _EMERGENCY_MAX_POSITION.get(new_regime, 0.60)
        snapshot_data = {
            "snapshot_id": f"DRIFT-{datetime.now().strftime('%Y%m%d%H%M')}-{uuid.uuid4().hex[:4]}",
            "trade_date": datetime.now().strftime("%Y-%m-%d"),
            "regime": new_regime,
            "risk_appetite": "low" if new_regime in ("bear", "panic") else "medium",
            "recommended_posture": "defend" if new_regime in ("bear", "panic") else "observe",
            "max_total_position_pct": max_pos,
            "hot_sectors": [],
            "summary": f"盘中 regime drift 检测: 市场环境切换至 {new_regime}",
            "evidence": evidence,
            "created_at": datetime.now().isoformat(),
            "is_drift_update": True,
        }
        session_id = f"drift-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.brain.store.save_market_regime(session_id, snapshot_data)
        logger.info("Regime 已更新: %s (drift triggered)", new_regime)

    def _trigger_emergency_review(self, new_regime: str) -> None:
        """触发盘中紧急复审（异步，不阻塞 drift 检测）。"""
        logger.warning(
            "触发紧急复审! 市场恶化至 %s — 将对所有持仓重新评估", new_regime,
        )
        # 设置标记，由下一次 intraday_review 读取并优先处理
        self.brain.log_agent_event(
            session_id=f"emergency-{datetime.now().strftime('%Y%m%d-%H%M')}",
            agent="regime_drift_detector",
            event_type="emergency_review_requested",
            payload={
                "reason": f"regime_drift_to_{new_regime}",
                "urgency": "high",
                "requested_at": datetime.now().isoformat(),
            },
        )


# Module-level singleton
_detector: RegimeDriftDetector | None = None


def get_drift_detector() -> RegimeDriftDetector:
    """获取全局 detector 实例。"""
    global _detector
    if _detector is None:
        _detector = RegimeDriftDetector()
    return _detector


def detect_regime_drift() -> dict[str, Any]:
    """便捷函数: 执行一次 drift 检测。"""
    return get_drift_detector().detect()
