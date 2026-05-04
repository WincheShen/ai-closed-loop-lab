"""Explorer Scanner — 探索者核心实现。

职责：
1. 每日 15:30 后自动抓取全市场 5000 只票的 K 线、资金流向、龙虎榜
2. 运行 Qlib 预设模型进行 Alpha 预测，筛选 Top 50
3. 结合 Social-Media-Automation 抓取的实时热点，交叉验证
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta
from typing import Any

from src.central_brain import get_central_brain
from src.graph.state import StockCandidate, TradingState
from src.infra.config import cfg
from src.infra.logger import get_agent_logger

logger = get_agent_logger("explorer", "init")


class ExplorerScanner:
    """探索者扫描器。

    目前使用占位实现，后续接入 AkShare + Qlib。
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.logger = get_agent_logger("explorer", session_id)
        self.brain = get_central_brain()

    def scan_market(self, date_str: str | None = None) -> list[StockCandidate]:
        """执行全市场扫描，返回候选股票列表。

        Args:
            date_str: 扫描日期 (YYYY-MM-DD)，默认昨天收盘日
        """
        scan_date = date_str or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        self.logger.info("开始全市场扫描 — date=%s", scan_date)

        # TODO: 接入 AkShare 获取全市场K线
        # TODO: 接入 Qlib 运行 Alpha 预测
        # TODO: 接入 Social-Media-Automation 的热点数据

        # --- 占位实现：模拟 Top 50 候选 ---
        candidates = self._mock_candidates(scan_date)

        self.logger.info("扫描完成 — 候选票 %d 只", len(candidates))
        self.brain.log_agent_event(
            self.session_id,
            "explorer",
            "scan_complete",
            {"date": scan_date, "candidate_count": len(candidates), "top_sectors": list({c["sector"] for c in candidates[:10]})},
        )
        return candidates

    def fetch_hot_sectors(self) -> list[str]:
        """获取当前热点板块（从社交媒体数据或外部数据源）。"""
        # TODO: 从 Social-Media-Automation 的热点缓存读取
        # 占位
        return ["低空经济", "AI手机", "固态电池", "人形机器人", "商业航天"]

    def cross_validate_with_sentiment(
        self, candidates: list[StockCandidate], hot_sectors: list[str]
    ) -> list[StockCandidate]:
        """将 Qlib 评分与社交情绪热点交叉验证，过滤"无逻辑支撑"的票。"""
        validated = []
        for c in candidates:
            sector_match = c["sector"] in hot_sectors or any(
                reason for reason in c.get("hot_reason", []) if any(hs in reason for hs in hot_sectors)
            )
            if sector_match or c["qlib_score"] > 0.85:
                validated.append(c)
        self.logger.info("交叉验证后剩余 %d / %d", len(validated), len(candidates))
        return validated

    def _mock_candidates(self, scan_date: str) -> list[StockCandidate]:
        """生成模拟候选数据（开发调试用）。"""
        sectors = ["低空经济", "AI手机", "固态电池", "人形机器人", "商业航天"]
        names = [
            ("万丰奥威", "002085.SZ"), ("宗申动力", "001696.SZ"),
            ("中兴通讯", "000063.SZ"), ("欧菲光", "002456.SZ"),
            ("宁德时代", "300750.SZ"), ("赣锋锂业", "002460.SZ"),
            ("优必选", "09880.HK"), ("鸣志电器", "603728.SH"),
            ("航天晨光", "600501.SH"), ("中国卫星", "600118.SH"),
        ]
        candidates: list[StockCandidate] = []
        for name, symbol in names:
            sector = random.choice(sectors)
            score = round(random.uniform(0.60, 0.95), 3)
            candidates.append({
                "symbol": symbol,
                "name": name,
                "qlib_score": score,
                "sector": sector,
                "hot_reason": [f"{sector}概念股，近期资金持续流入"],
                "kline_summary": {"trend": "up", "ma20": "support"},
                "fund_flow": {"main_net_inflow": round(random.uniform(1e7, 5e8), 0)},
                "dragon_tiger": None,
            })
        # 按 Qlib 分数降序
        candidates.sort(key=lambda x: x["qlib_score"], reverse=True)
        return candidates[:50]


def run_discovery_node(state: TradingState) -> dict[str, Any]:
    """LangGraph 节点函数 — 探索者扫描。

    输入：TradingState (空或含上次状态)
    输出：{"target_stocks": [...], "hot_sectors": [...]}
    """
    session_id = state["session_id"]
    scanner = ExplorerScanner(session_id)

    hot_sectors = scanner.fetch_hot_sectors()
    candidates = scanner.scan_market()
    validated = scanner.cross_validate_with_sentiment(candidates, hot_sectors)

    return {
        "target_stocks": validated,
        "hot_sectors": hot_sectors,
        "timestamp": datetime.now().isoformat(),
        "logs": state.get("logs", []) + [f"[Explorer] 扫描完成: {len(validated)} 只候选票"],
    }
