"""选股规则引擎。

设计要点（对应需求 FR-1.2）：
- 每条规则有 id / name / enabled / weight / params
- 内置规则集合可扩展（builtin.py 注册）
- 规则函数签名：(stock, params) -> bool 是否命中
- 多规则用加权投票合并：score = Σ enabled.weight × hit
- YAML 文件作为规则配置入口（人工可维护）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from ..data_source.akshare_client import StockQuote

logger = logging.getLogger(__name__)


# (stock, params) -> bool
RuleFunc = Callable[[StockQuote, dict], bool]


@dataclass
class Rule:
    id: str
    name: str
    func: RuleFunc
    enabled: bool = True
    weight: float = 1.0
    params: dict = field(default_factory=dict)
    description: str = ""


@dataclass
class RuleResult:
    stock: StockQuote
    score: float
    matched_rule_ids: list[str]
    detail: dict[str, bool]  # rule_id -> hit


# ---------------------------------------------------------------------------
# 全局注册表
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, RuleFunc] = {}


def register(rule_id: str) -> Callable[[RuleFunc], RuleFunc]:
    """装饰器：把规则函数注册到全局表，供 YAML 引用。"""
    def deco(func: RuleFunc) -> RuleFunc:
        if rule_id in _REGISTRY:
            logger.warning("rule '%s' already registered, overwriting", rule_id)
        _REGISTRY[rule_id] = func
        return func
    return deco


def get_registered(rule_id: str) -> Optional[RuleFunc]:
    return _REGISTRY.get(rule_id)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class RuleEngine:
    def __init__(self, rules: list[Rule]):
        self.rules = rules

    @property
    def enabled_rules(self) -> list[Rule]:
        return [r for r in self.rules if r.enabled]

    def evaluate(self, stock: StockQuote) -> RuleResult:
        score = 0.0
        matched: list[str] = []
        detail: dict[str, bool] = {}
        for rule in self.enabled_rules:
            try:
                hit = bool(rule.func(stock, rule.params))
            except Exception as e:  # noqa: BLE001
                logger.warning("rule %s failed on %s: %s", rule.id, stock.symbol, e)
                hit = False
            detail[rule.id] = hit
            if hit:
                score += rule.weight
                matched.append(rule.id)
        return RuleResult(stock=stock, score=score, matched_rule_ids=matched, detail=detail)

    def filter_and_rank(
        self,
        stocks: list[StockQuote],
        min_score: float = 1.0,
        top_k: int = 50,
    ) -> list[RuleResult]:
        results = [self.evaluate(s) for s in stocks]
        passed = [r for r in results if r.score >= min_score]
        passed.sort(key=lambda r: r.score, reverse=True)
        return passed[:top_k]


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

def load_rules_from_yaml(path: Path | str) -> list[Rule]:
    """从 YAML 加载规则。

    YAML 结构：
        version: 1
        rules:
          - id: volume_breakout
            name: 量能突破
            enabled: true
            weight: 1.0
            params: {ratio: 1.2}
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"rules yaml not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    rules: list[Rule] = []
    for entry in data.get("rules", []):
        rule_id = entry["id"]
        func = get_registered(rule_id)
        if func is None:
            logger.warning("rule id '%s' not registered, skipping", rule_id)
            continue
        rules.append(Rule(
            id=rule_id,
            name=entry.get("name", rule_id),
            func=func,
            enabled=entry.get("enabled", True),
            weight=float(entry.get("weight", 1.0)),
            params=entry.get("params", {}) or {},
            description=entry.get("description", ""),
        ))
    logger.info("loaded %d rules from %s", len(rules), path)
    return rules
