"""策略编译器 — 自然语言策略 → 结构化选股规则。

LLM 将用户输入的策略文字解析为可执行的 StrategySpec，包含：
- filters: 硬性过滤条件
- rankings: 排序偏好
- technicals: 技术面要求（文本描述，后续可扩展）
- limit: 最大返回数量
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class FilterCondition:
    """单个过滤条件。"""
    field: str          # pe_ttm, pb, market_cap_yi, industry, change_pct, ...
    op: str             # <=, >=, ==, !=, in, contains, between
    value: Any          # 数值 / 字符串 / 列表
    description: str = ""  # LLM 对该条件的自然语言解释


@dataclass
class RankingPreference:
    """排序偏好。"""
    field: str          # roe, pe_ttm, change_pct, turnover_rate, ...
    direction: str = "desc"   # asc / desc
    weight: float = 1.0
    description: str = ""


@dataclass
class StrategySpec:
    """结构化策略规格。"""
    name: str = ""
    description: str = ""
    filters: list[FilterCondition] = field(default_factory=list)
    rankings: list[RankingPreference] = field(default_factory=list)
    technicals: list[str] = field(default_factory=list)  # 技术面描述（MVP 暂不执行）
    limit: int = 20
    explanation: str = ""  # LLM 对整体策略的解读

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "filters": [
                {"field": f.field, "op": f.op, "value": f.value, "description": f.description}
                for f in self.filters
            ],
            "rankings": [
                {"field": r.field, "direction": r.direction, "weight": r.weight, "description": r.description}
                for r in self.rankings
            ],
            "technicals": self.technicals,
            "limit": self.limit,
            "explanation": self.explanation,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StrategySpec":
        return cls(
            name=d.get("name", ""),
            description=d.get("description", ""),
            filters=[
                FilterCondition(
                    field=f["field"], op=f["op"], value=f["value"],
                    description=f.get("description", "")
                )
                for f in d.get("filters", [])
            ],
            rankings=[
                RankingPreference(
                    field=r["field"], direction=r.get("direction", "desc"),
                    weight=r.get("weight", 1.0), description=r.get("description", "")
                )
                for r in d.get("rankings", [])
            ],
            technicals=d.get("technicals", []),
            limit=d.get("limit", 20),
            explanation=d.get("explanation", ""),
        )


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
你是一个 A 股选股策略分析师。用户会给你一段自然语言描述的选股策略，
你需要将其解析为结构化的 JSON 格式。

可用的过滤字段 (filters):
- pe_ttm: 市盈率（动态）
- pb: 市净率
- market_cap_yi: 总市值（亿元）
- change_pct: 当日涨跌幅（%）
- turnover_rate: 换手率（%）
- volume: 成交量（手）
- turnover: 成交额（元），1亿=100000000
- main_fund_net_inflow: 主力净流入（元），1亿=100000000
- industry: 所属行业/板块名称（东方财富分类，如"半导体"、"新能源汽车"、"医疗器械"等）
- name: 股票名称
- symbol: 股票代码

可用的排序字段 (rankings):
- 与 filters 相同的字段
- rule_score: 规则综合评分

操作符 (op):
- 数值: <=, >=, <, >, ==, !=, between
- 字符串模糊匹配: contains（推荐用于 industry/name）
- 字符串精确列表: in, not_in（仅当值与数据完全一致时使用）

【重要】industry 字段规则：
- 数据是东方财富板块名称，必须用 contains 做模糊匹配，禁止用 in
- AI/人工智能 → contains "人工智能" 或 "算力"
- 半导体/芯片 → contains "半导体"
- 新能源/锂电/储能 → contains "新能源" 或 "储能"
- 医药/创新药 → contains "医药" 或 "创新药"
- 军工/航天 → contains "军工"
- 多个行业用多条 filter 条件（每条 contains 一个关键词）

输出 JSON 格式：
{
    "name": "策略名称（简短）",
    "description": "策略完整描述",
    "filters": [
        {"field": "字段名", "op": "操作符", "value": 值, "description": "解释"}
    ],
    "rankings": [
        {"field": "字段名", "direction": "desc/asc", "weight": 权重, "description": "解释"}
    ],
    "technicals": ["技术面描述1", "技术面描述2"],
    "limit": 20,
    "explanation": "对策略的整体解读和建议"
}

注意：
1. 只输出纯 JSON，不要加 markdown 代码块
2. 需要历史K线才能判断的条件（如"创新高"、"回调N日"、"站上均线"），放到 technicals 中
3. 对每个 filter 和 ranking 都给出中文 description 解释
4. 如果用户没有明确指定数值，给出合理的默认值
5. explanation 中简要说明策略解读和潜在风险
"""


class StrategyCompiler:
    """使用 LLM 将自然语言策略编译为 StrategySpec。"""

    def __init__(self, llm=None):
        """
        Args:
            llm: langchain ChatModel 实例。若为 None 则延迟初始化。
        """
        self._llm = llm

    def _get_llm(self):
        if self._llm is None:
            from infra.model_adapter import get_llm
            # 使用 gpt-5.4-mini（Azure 上已部署的轻量模型）
            self._llm = get_llm(model_name="gpt-5.4-mini")
        return self._llm

    def compile(self, user_strategy: str) -> StrategySpec:
        """将自然语言策略编译为 StrategySpec。

        Args:
            user_strategy: 用户输入的策略描述文字

        Returns:
            StrategySpec 结构化规格
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = self._get_llm()
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"请解析以下选股策略：\n\n{user_strategy}"),
        ]

        logger.info("Compiling strategy: %s", user_strategy[:100])
        response = llm.invoke(messages)
        raw_text = response.content.strip()

        # 尝试提取 JSON（处理可能的 markdown 包裹）
        json_text = raw_text
        if "```" in json_text:
            lines = json_text.split("\n")
            in_block = False
            block_lines = []
            for line in lines:
                if line.strip().startswith("```"):
                    if in_block:
                        break
                    in_block = True
                    continue
                if in_block:
                    block_lines.append(line)
            if block_lines:
                json_text = "\n".join(block_lines)

        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.error("LLM output is not valid JSON: %s\nRaw: %s", e, raw_text[:500])
            # 返回一个基础 spec
            return StrategySpec(
                name="解析失败",
                description=user_strategy,
                explanation=f"LLM 输出解析失败: {e}. 原始输出: {raw_text[:200]}",
            )

        spec = StrategySpec.from_dict(parsed)
        logger.info("Strategy compiled: name=%s, filters=%d, rankings=%d",
                    spec.name, len(spec.filters), len(spec.rankings))
        return spec

    def compile_mock(self, user_strategy: str) -> StrategySpec:
        """Mock 编译（不调用 LLM），用于测试。"""
        # 简单关键词匹配
        filters = []
        rankings = []

        text = user_strategy.lower()

        if "pe" in text or "市盈" in text:
            filters.append(FilterCondition("pe_ttm", "<=", 30, "市盈率不超过30"))
        if "pb" in text or "市净" in text:
            filters.append(FilterCondition("pb", "<=", 5, "市净率不超过5"))
        if "半导体" in text:
            filters.append(FilterCondition("industry", "contains", "半导体", "行业含半导体"))
        if "新能源" in text:
            filters.append(FilterCondition("industry", "contains", "新能源", "行业含新能源"))
        if "roe" in text:
            rankings.append(RankingPreference("pe_ttm", "asc", 1.5, "低PE优先"))
        if "放量" in text or "成交" in text:
            filters.append(FilterCondition("turnover_rate", ">=", 3.0, "换手率不低于3%"))
        if "主力" in text or "资金" in text:
            filters.append(FilterCondition("main_fund_net_inflow", ">", 0, "主力净流入为正"))

        if not filters:
            filters.append(FilterCondition("market_cap_yi", ">=", 50, "市值不低于50亿"))
            filters.append(FilterCondition("pe_ttm", "<=", 50, "市盈率不超过50"))

        if not rankings:
            rankings.append(RankingPreference("change_pct", "desc", 1.0, "涨幅优先"))

        return StrategySpec(
            name="Mock策略",
            description=user_strategy,
            filters=filters,
            rankings=rankings,
            limit=20,
            explanation="[Mock模式] 基于关键词匹配生成的规则",
        )
