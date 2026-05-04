"""Prompt Evolution — 强化学习驱动的策略权重进化。

核心逻辑：
1. 将盈利/亏损结果作为 Reward 函数
2. 分析策略级胜率：哪个策略近期表现好/差
3. 动态调整 Prompt 中的策略权重描述

例如：
  "在最近 5 次交易中，缩量回踩逻辑亏损 4 次。
   建议下周降低缩量回踩的权重，增加突破追涨的权重。"
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.central_brain import get_central_brain
from src.graph.state import PerformanceRecord, PromptWeight
from src.infra.config import cfg
from src.infra.logger import get_agent_logger

logger = get_agent_logger("feedback_loop", "init")


class PromptEvolution:
    """Prompt 进化引擎。"""

    # 默认策略库
    DEFAULT_STRATEGIES = [
        "20日线回踩",
        "15分钟放量突破",
        "缩量企稳",
        "MACD金叉",
        "突破前高",
        "布林带中轨反弹",
    ]

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.logger = get_agent_logger("feedback_loop", session_id)
        self.brain = get_central_brain()
        self.weights_path = Path(cfg().get("data_dir", "data")) / "prompt_weights.json"

    def load_weights(self) -> list[PromptWeight]:
        """加载当前策略权重。"""
        if self.weights_path.exists():
            with open(self.weights_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [w for w in data if isinstance(w, dict)]
        # 初始化默认值
        return [
            {
                "strategy_name": s,
                "current_weight": 1.0 / len(self.DEFAULT_STRATEGIES),
                "win_count": 0,
                "loss_count": 0,
                "last_updated": datetime.now().isoformat(),
            }
            for s in self.DEFAULT_STRATEGIES
        ]

    def save_weights(self, weights: list[PromptWeight]) -> None:
        """保存策略权重。"""
        self.weights_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.weights_path, "w", encoding="utf-8") as f:
            json.dump(weights, f, ensure_ascii=False, indent=2)

    def update_weights_from_records(
        self, records: list[PerformanceRecord]
    ) -> list[PromptWeight]:
        """根据复盘记录更新策略权重。

        算法：
        - 每条记录关联一个策略（目前从 record.analysis 中解析）
        - 盈利 → win_count += 1
        - 亏损 → loss_count += 1
        - 权重 = win_count / (win_count + loss_count) 的 softmax 归一化
        """
        weights = self.load_weights()
        weight_map = {w["strategy_name"]: w for w in weights}

        for rec in records:
            # 从 analysis 文本中匹配策略名
            matched_strategy = None
            for strategy_name in weight_map:
                if strategy_name in rec.get("analysis", ""):
                    matched_strategy = strategy_name
                    break

            if not matched_strategy:
                continue

            w = weight_map[matched_strategy]
            if rec["actual_return"] > 0:
                w["win_count"] += 1
            else:
                w["loss_count"] += 1
            w["last_updated"] = datetime.now().isoformat()

        # 重新归一化权重
        total_score = 0.0
        for w in weights:
            total = w["win_count"] + w["loss_count"]
            if total > 0:
                w["current_weight"] = w["win_count"] / total
            else:
                w["current_weight"] = 0.5  # 无数据时中性
            total_score += w["current_weight"]

        # softmax 归一化到和为 1
        if total_score > 0:
            for w in weights:
                w["current_weight"] = round(w["current_weight"] / total_score, 4)

        self.save_weights(weights)

        self.logger.info(
            "策略权重已更新 — %s",
            ", ".join(f"{w['strategy_name']}:{w['current_weight']:.3f}" for w in weights),
        )
        return weights

    def generate_evolution_prompt(self, weights: list[PromptWeight]) -> str:
        """生成给 Strategist 的进化后 Prompt 片段。

        返回一段自然语言描述，可直接拼接到 Strategist 的系统 Prompt 中。
        """
        lines = ["\n📊 策略权重反馈（基于最近实盘表现）：\n"]
        for w in weights:
            total = w["win_count"] + w["loss_count"]
            if total >= 3:  # 至少 3 次样本才给出明确建议
                perf = f"胜率 {w['win_count']}/{total} = {w['win_count']/total:.0%}"
                if w["current_weight"] > 0.25:
                    advice = "✅ 表现优异，保持高权重"
                elif w["current_weight"] < 0.1:
                    advice = "❌ 连续亏损，建议降低权重或暂停"
                else:
                    advice = "➡️ 表现一般，维持观察"
                lines.append(f"  • {w['strategy_name']}: {perf} → {advice}")
            else:
                lines.append(f"  • {w['strategy_name']}: 样本不足 ({total}次)，暂不调整")

        lines.append("\n🎯 下周建议：优先使用权重 > 0.2 的策略，谨慎使用权重 < 0.1 的策略。")
        return "\n".join(lines)

    def ingest_comments(self, comments: list[dict]) -> list[dict]:
        """将社交媒体评论区的高质量观点整理为"外部观察"。

        输入：fan_feedback (Comment 列表的简化 dict)
        输出：提炼后的洞察列表，输入给 Explorer 作为新分析维度。
        """
        insights = []
        for c in comments:
            content = c.get("content", "")
            score = c.get("quality_score", 0)
            if score > 0.7:  # 高质量评论阈值
                insight = {
                    "source": "social_comment",
                    "author": c.get("author", "anonymous"),
                    "raw": content,
                    "extracted": c.get("extracted_insight", ""),
                    "timestamp": c.get("created_at", datetime.now().isoformat()),
                }
                insights.append(insight)

        self.logger.info("从评论中提取 %d 条高质量洞察", len(insights))
        return insights
