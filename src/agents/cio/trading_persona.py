"""Trading Persona — 投资人格定义与加载。

Trading Persona 是 Cognitive Agent 的"性格"，定义其长期交易风格和行为边界。
每日决策都必须遵守 persona 的约束，防止 LLM 因上下文变化导致风格漂移。

Phase 1 目标：
- 加载 config/trading_persona.yaml
- 提供给 Strategist 作为 prompt context
- 提供给 RiskGovernor 作为风控边界
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.infra.logger import get_logger

logger = get_logger(__name__)

_PERSONA_YAML = Path("config/trading_persona.yaml")


@dataclass
class TradingPersona:
    """投资人格 — 决策风格与风控边界的统一定义。"""

    id: str
    version: str
    name: str
    capital: int = 0  # 资金规模（元）
    philosophy: list[str] = field(default_factory=list)
    preferred_holding_days: list[int] = field(default_factory=lambda: [1, 5])
    preferred_setups: list[str] = field(default_factory=list)
    avoid_setups: list[str] = field(default_factory=list)
    risk_limits: dict[str, Any] = field(default_factory=dict)
    strategy_regime_compatibility: dict[str, dict[str, list[str]]] = field(
        default_factory=dict,
    )
    social_style: dict[str, Any] = field(default_factory=dict)
    stock_selection_rules: dict[str, Any] = field(default_factory=dict)
    config_hash: str = ""

    @property
    def persona_version(self) -> str:
        """对外暴露的版本标识 = version + config_hash[:8]，便于追溯。"""
        return f"{self.version}+{self.config_hash[:8]}" if self.config_hash else self.version

    def max_total_position_for(self, regime: str) -> float:
        """根据 market regime 获取总仓位上限。"""
        limits = self.risk_limits.get("max_total_position_pct", {})
        if isinstance(limits, dict):
            return float(limits.get(regime, limits.get("neutral", 0.4)))
        return float(limits or 0.4)

    @property
    def max_single_position_pct(self) -> float:
        return float(self.risk_limits.get("max_single_position_pct", 0.10))

    @property
    def max_sector_concentration_pct(self) -> float:
        return float(self.risk_limits.get("max_sector_concentration_pct", 0.30))

    @property
    def default_stop_loss_pct(self) -> float:
        return float(self.risk_limits.get("default_stop_loss_pct", 0.05))

    @property
    def default_take_profit_pct(self) -> float:
        return float(self.risk_limits.get("default_take_profit_pct", 0.10))

    @property
    def max_daily_loss_pct(self) -> float:
        return float(self.risk_limits.get("max_daily_loss_pct", 0.03))

    @property
    def consecutive_loss_limit(self) -> int:
        return int(self.risk_limits.get("consecutive_loss_limit", 3))

    def is_strategy_allowed(self, strategy_id: str, regime: str) -> str:
        """检查策略在当前 regime 下是否允许。

        Returns:
            "allowed" / "degraded" / "forbidden"
        """
        compat = self.strategy_regime_compatibility.get(strategy_id, {})
        if not compat:
            return "allowed"  # 未配置则默认允许
        if regime in compat.get("forbidden", []):
            return "forbidden"
        if regime in compat.get("degraded", []):
            return "degraded"
        if regime in compat.get("compatible", []):
            return "allowed"
        return "allowed"

    def prompt_summary(self) -> str:
        """生成 LLM prompt 用的人格摘要（简洁版）。"""
        lines = [
            f"## 你的交易人格: {self.name} ({self.id})",
            f"哲学: {'; '.join(self.philosophy)}",
            f"持仓周期: {self.preferred_holding_days[0]}-{self.preferred_holding_days[-1]} 个交易日",
            f"偏好: {', '.join(self.preferred_setups)}",
            f"禁忌: {', '.join(self.avoid_setups)}",
            f"风控: 单票≤{self.max_single_position_pct:.0%}, "
            f"止损{self.default_stop_loss_pct:.0%}, "
            f"目标{self.default_take_profit_pct:.0%}",
        ]
        return "\n".join(lines)


def load_persona(path: Path | str | None = None, persona_id: str | None = None) -> TradingPersona:
    """从 YAML 加载投资人格。失败时返回内置默认 persona。

    Args:
        path: YAML 文件路径
        persona_id: 指定加载的人格 ID，如果为 None 则加载第一个人格
    """
    yaml_path = Path(path) if path else _PERSONA_YAML
    if not yaml_path.exists():
        logger.warning("trading_persona.yaml 不存在 (%s)，使用默认 persona", yaml_path)
        return _default_persona()

    try:
        raw = yaml_path.read_bytes()
        config_hash = hashlib.sha256(raw).hexdigest()
        data = yaml.safe_load(raw) or {}

        # 支持旧格式（单个 persona）和新格式（多个 personas）
        personas_data = data.get("personas", [])
        if not personas_data:
            # 旧格式兼容
            p = data.get("persona", {})
            personas_data = [p] if p else []

        if not personas_data:
            logger.warning("未找到 persona 配置，使用默认 persona")
            return _default_persona()

        # 选择要加载的 persona
        if persona_id:
            p = next((p for p in personas_data if p.get("id") == persona_id), None)
            if not p:
                logger.warning("未找到 persona_id=%s，使用第一个 persona", persona_id)
                p = personas_data[0]
        else:
            p = personas_data[0]

        return TradingPersona(
            id=p.get("id", "default_v1"),
            version=p.get("version", "v1.0"),
            name=p.get("name", "默认交易员"),
            capital=p.get("capital", 0),
            philosophy=list(p.get("philosophy", [])),
            preferred_holding_days=list(p.get("preferred_holding_days", [1, 5])),
            preferred_setups=list(p.get("preferred_setups", [])),
            avoid_setups=list(p.get("avoid_setups", [])),
            risk_limits=dict(p.get("risk_limits", {})),
            strategy_regime_compatibility=dict(
                p.get("strategy_regime_compatibility", {}),
            ),
            social_style=dict(p.get("social_style", {})),
            stock_selection_rules=dict(p.get("stock_selection_rules", {})),
            config_hash=config_hash,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("加载 persona 失败: %s，使用默认 persona", e)
        return _default_persona()


def load_persona_by_id(persona_id: str, path: Path | str | None = None) -> TradingPersona:
    """按 ID 加载指定人格。"""
    return load_persona(path, persona_id)


def list_personas(path: Path | str | None = None) -> list[dict[str, Any]]:
    """列出所有可用的人格。"""
    yaml_path = Path(path) if path else _PERSONA_YAML
    if not yaml_path.exists():
        return []

    try:
        data = yaml.safe_load(yaml_path.read_bytes()) or {}
        personas_data = data.get("personas", [])
        if not personas_data:
            # 旧格式兼容
            p = data.get("persona", {})
            personas_data = [p] if p else []

        return [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "version": p.get("version", "v1.0"),
                "capital": p.get("capital", 0),
                "philosophy": p.get("philosophy", []),
                "preferred_holding_days": p.get("preferred_holding_days", [1, 5]),
                "preferred_setups": p.get("preferred_setups", []),
                "avoid_setups": p.get("avoid_setups", []),
                "risk_limits": p.get("risk_limits", {}),
                "strategy_regime_compatibility": p.get("strategy_regime_compatibility", {}),
                "social_style": p.get("social_style", {}),
                "stock_selection_rules": p.get("stock_selection_rules", {}),
            }
            for p in personas_data
        ]
    except Exception as e:  # noqa: BLE001
        logger.error("列出 personas 失败: %s", e)
        return []


def _default_persona() -> TradingPersona:
    """内置默认人格（短线热点轮动）。"""
    return TradingPersona(
        id="default_v1",
        version="v1.0",
        name="默认短线交易员",
        philosophy=["先判断市场，再决定交易"],
        risk_limits={
            "max_single_position_pct": 0.10,
            "max_sector_concentration_pct": 0.30,
            "max_total_position_pct": {
                "bull": 0.70, "neutral": 0.40, "bear": 0.15,
                "panic": 0.05, "rebound": 0.50,
            },
            "default_stop_loss_pct": 0.05,
            "default_take_profit_pct": 0.10,
            "max_daily_loss_pct": 0.03,
            "consecutive_loss_limit": 3,
        },
    )


_persona_cache: dict[str, TradingPersona] = {}


def get_persona(persona_id: str | None = None, reload: bool = False) -> TradingPersona:
    """获取 persona。如果指定 persona_id 则加载指定人格，否则加载第一个。

    Args:
        persona_id: 人格 ID，如果为 None 则加载第一个人格
        reload: 是否重新加载
    """
    global _persona_cache
    key = persona_id or "default"

    if key not in _persona_cache or reload:
        _persona_cache[key] = load_persona(persona_id=persona_id)
    return _persona_cache[key]
