"""Explorer Scanner — 探索者核心实现。

职责：
1. 每日收盘后抓取全市场行情快照（AKShare 真实数据，失败时降级 mock）
2. 检测热点板块 Top 5
3. 运行规则引擎筛选候选票 → Top 30
4. 拉取候选票近期 K 线，生成走势摘要供 Strategist 使用
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.central_brain import get_central_brain
from src.graph.state import StockCandidate, TradingState
from src.infra.logger import get_agent_logger
from src.stock_analyzer.data_source import AkshareClient, HotSectorDetector
from src.stock_analyzer.rules import RuleEngine, load_rules_from_yaml

logger = get_agent_logger("explorer", "init")

_RULES_YAML = Path("config/rules.yaml")
_KLINE_ENRICH_LIMIT = 10


class ExplorerScanner:
    """探索者扫描器 — AKShare 行情 + 规则引擎 + 热点检测。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.logger = get_agent_logger("explorer", session_id)
        self.brain = get_central_brain()
        self.akshare = AkshareClient(allow_mock_fallback=True)
        self.hot_detector = HotSectorDetector()
        self._snapshot = None
        self._hot_names: list[str] = []

    def scan_market(self, date_str: str | None = None) -> list[StockCandidate]:
        """全市场扫描 → 热点检测 → 规则引擎 → 候选票列表。"""
        self.logger.info("开始全市场扫描")

        # 1. 全市场快照（真实 AKShare 数据，失败降级 mock）
        self._snapshot = self.akshare.fetch_snapshot()
        self.logger.info(
            "行情快照: date=%s mock=%s stocks=%d sectors=%d",
            self._snapshot.snapshot_date, self._snapshot.is_mock,
            len(self._snapshot.stocks), len(self._snapshot.sectors),
        )

        # 2. 热点板块 Top 5
        hot_results = self.hot_detector.detect(self._snapshot, top_k=5)
        self._hot_names = [h.sector.name for h in hot_results]
        self.logger.info("热点板块: %s", self._hot_names)

        # 3. 规则引擎筛选
        rules = load_rules_from_yaml(_RULES_YAML)
        for rule in rules:
            if rule.id == "in_hot_sector":
                rule.params = {**rule.params, "hot_sectors": self._hot_names}
        engine = RuleEngine(rules)
        results = engine.filter_and_rank(
            self._snapshot.stocks, min_score=2.0, top_k=30,
        )
        self.logger.info("规则引擎筛选: %d 只通过", len(results))

        # 4. 转化为 StockCandidate（前 N 只附带 K 线摘要）
        max_score = results[0].score if results else 1.0
        candidates: list[StockCandidate] = []
        for idx, r in enumerate(results):
            stock = r.stock
            kline = (
                self._build_kline_summary(stock.symbol, stock.price)
                if idx < _KLINE_ENRICH_LIMIT
                else {"current_price": stock.price, "trend": "not_fetched"}
            )
            candidates.append({
                "symbol": stock.symbol,
                "name": stock.name,
                "qlib_score": round(r.score / max(max_score, 1.0), 3),
                "sector": stock.industry or "未知",
                "hot_reason": [
                    f"规则匹配: {', '.join(r.matched_rule_ids)}",
                    *(
                        [f"属于热点板块「{stock.industry}」"]
                        if stock.industry in self._hot_names else []
                    ),
                ],
                "kline_summary": {
                    **kline,
                    "change_pct": stock.change_pct,
                    "pe_ttm": stock.pe_ttm,
                    "pb": stock.pb,
                    "market_cap_yi": stock.market_cap_yi,
                },
                "fund_flow": {
                    "main_net_inflow": stock.main_fund_net_inflow,
                    "turnover": stock.turnover,
                    "turnover_rate": stock.turnover_rate,
                },
                "dragon_tiger": None,
            })

        self.logger.info("扫描完成 — 候选票 %d 只", len(candidates))
        self.brain.log_agent_event(
            self.session_id, "explorer", "scan_complete",
            {
                "date": str(self._snapshot.snapshot_date),
                "candidate_count": len(candidates),
                "top_sectors": self._hot_names,
                "is_mock": self._snapshot.is_mock,
            },
        )
        return candidates

    def fetch_hot_sectors(self) -> list[str]:
        """获取当前热点板块（使用 HotSectorDetector 从行情数据检测）。"""
        if self._hot_names:
            return self._hot_names
        snapshot = self._snapshot or self.akshare.fetch_snapshot()
        hot = self.hot_detector.detect(snapshot, top_k=5)
        self._hot_names = [h.sector.name for h in hot]
        return self._hot_names

    def cross_validate_with_sentiment(
        self, candidates: list[StockCandidate], hot_sectors: list[str],
    ) -> list[StockCandidate]:
        """已由规则引擎筛选，直接透传。"""
        self.logger.info("候选票 %d 只（规则引擎已筛选）", len(candidates))
        return candidates

    def _build_kline_summary(self, symbol: str, current_price: float) -> dict:
        """拉取近 20 日 K 线，生成数值摘要供 Strategist LLM 分析。"""
        try:
            bars = self.akshare.fetch_kline(symbol, days=20)
            if not bars:
                return {"current_price": current_price, "trend": "no_data"}

            closes = [b.close for b in bars]
            volumes = [b.volume for b in bars]
            n = len(closes)

            ma5 = sum(closes[-5:]) / min(5, n) if n >= 1 else 0
            ma10 = sum(closes[-10:]) / min(10, n) if n >= 1 else 0
            ma20 = sum(closes) / n if n >= 1 else 0
            avg_vol = sum(volumes) / n if n >= 1 else 1
            latest_vol = volumes[-1] if volumes else 0

            return {
                "current_price": current_price,
                "last_close": round(closes[-1], 2) if closes else current_price,
                "ma5": round(ma5, 2),
                "ma10": round(ma10, 2),
                "ma20": round(ma20, 2),
                "price_vs_ma5": "above" if current_price > ma5 else "below",
                "price_vs_ma20": "above" if current_price > ma20 else "below",
                "recent_5d_change_pct": round(
                    (closes[-1] / closes[-6] - 1) * 100, 2,
                ) if n >= 6 else 0,
                "recent_high_10d": round(max(b.high for b in bars[-10:]), 2),
                "recent_low_10d": round(min(b.low for b in bars[-10:]), 2),
                "vol_ratio": round(latest_vol / avg_vol, 2) if avg_vol > 0 else 1,
                "trend": "up" if n >= 5 and closes[-1] > closes[-5] else "down",
            }
        except Exception as e:
            self.logger.warning("K线摘要失败 %s: %s", symbol, e)
            return {"current_price": current_price, "trend": "error"}


def run_discovery_node(state: TradingState) -> dict[str, Any]:
    """LangGraph 节点函数 — 探索者扫描。

    输入：TradingState (空或含上次状态)
    输出：{"target_stocks": [...], "hot_sectors": [...]}
    """
    session_id = state["session_id"]
    scanner = ExplorerScanner(session_id)

    candidates = scanner.scan_market()
    hot_sectors = scanner.fetch_hot_sectors()

    return {
        "target_stocks": candidates,
        "hot_sectors": hot_sectors,
        "timestamp": datetime.now().isoformat(),
        "logs": state.get("logs", []) + [
            f"[Explorer] 扫描完成: {len(candidates)} 只候选票, "
            f"热点: {', '.join(hot_sectors)}"
        ],
    }
