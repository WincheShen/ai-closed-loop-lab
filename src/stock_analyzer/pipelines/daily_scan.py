"""每日选股流水线。

流程（对应需求 FR-1.1）：
    1. AKShare 全市场快照
    2. 热点板块 Top5
    3. 规则引擎筛选候选 → Top N
    4. 调用 TradingAgent /analyze（HTTP，可选）
    5. 交易员综合：激进 1-3 只 + 稳健 1-3 只
    6. 持久化 + 输出 JSON
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import httpx

from ..data_source import AkshareClient, HotSectorDetector
from ..data_source.akshare_client import StockQuote
from ..rules import RuleEngine, load_rules_from_yaml
from ..rules import builtin  # noqa: F401  触发 @register 装饰器执行

logger = logging.getLogger(__name__)


@dataclass
class RecommendedStock:
    symbol: str
    name: str
    price: float
    change_pct: float
    industry: str
    rule_score: float
    matched_rules: list[str]
    # TradingAgent 报告（可选）
    agent_decision: Optional[str] = None
    agent_confidence: Optional[float] = None
    agent_summary: Optional[str] = None
    # 交易员归类
    bucket: str = "candidate"  # candidate | aggressive | stable
    reasoning: str = ""


@dataclass
class DailyPicks:
    pick_date: date
    is_mock_data: bool
    hot_sectors: list[str]
    aggressive: list[RecommendedStock] = field(default_factory=list)
    stable: list[RecommendedStock] = field(default_factory=list)
    candidates: list[RecommendedStock] = field(default_factory=list)

    def to_json(self) -> str:
        d = asdict(self)
        d["pick_date"] = self.pick_date.isoformat()
        return json.dumps(d, ensure_ascii=False, indent=2)


class DailyScanPipeline:
    def __init__(
        self,
        rules_yaml: Path | str = "config/rules.yaml",
        trading_agent_url: Optional[str] = "http://localhost:8001",
        output_dir: Path | str = "data/daily_picks",
        candidate_top_k: int = 30,
        max_agent_calls: int = 8,
        # Phase 2: Social Media dispatcher
        sma_base_url: Optional[str] = None,
        sma_account_id: Optional[str] = None,
    ):
        self.rules_yaml = Path(rules_yaml)
        self.trading_agent_url = trading_agent_url
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.candidate_top_k = candidate_top_k
        self.max_agent_calls = max_agent_calls
        self.sma_base_url = sma_base_url
        self.sma_account_id = sma_account_id

        self.akshare = AkshareClient()
        self.hot_detector = HotSectorDetector()

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> DailyPicks:
        started = time.time()

        # 1. 行情快照
        snapshot = self.akshare.fetch_snapshot()
        logger.info(
            "snapshot date=%s mock=%s stocks=%d sectors=%d",
            snapshot.snapshot_date, snapshot.is_mock,
            len(snapshot.stocks), len(snapshot.sectors),
        )

        # 2. 热点板块
        hot = self.hot_detector.detect(snapshot, top_k=5)
        hot_names = [h.sector.name for h in hot]
        logger.info("hot sectors: %s", hot_names)

        # 3. 加载规则 + 注入热门板块参数
        rules = load_rules_from_yaml(self.rules_yaml)
        for rule in rules:
            if rule.id == "in_hot_sector":
                rule.params = {**rule.params, "hot_sectors": hot_names}
        engine = RuleEngine(rules)
        results = engine.filter_and_rank(
            snapshot.stocks, min_score=2.0, top_k=self.candidate_top_k
        )

        candidates = [self._to_recommend(r.stock, r.score, r.matched_rule_ids)
                      for r in results]

        # 4. TradingAgent 深度分析（限量，避免 LLM 费用爆炸）
        agent_calls = 0
        for stock in candidates[: self.max_agent_calls]:
            self._enrich_with_agent(stock)
            if stock.agent_decision is not None:
                agent_calls += 1

        # 5. 交易员综合归类
        aggressive, stable = self._traders_recommend(candidates)

        picks = DailyPicks(
            pick_date=snapshot.snapshot_date,
            is_mock_data=snapshot.is_mock,
            hot_sectors=hot_names,
            aggressive=aggressive,
            stable=stable,
            candidates=candidates,
        )

        # 6. 持久化
        out_path = self.output_dir / f"{snapshot.snapshot_date.isoformat()}.json"
        out_path.write_text(picks.to_json(), encoding="utf-8")
        logger.info("daily picks saved → %s", out_path)

        elapsed = time.time() - started

        # 6b. 归档到 Central Brain（Phase 3.5 observability）
        self._archive_to_central_brain(
            picks=picks,
            candidates_count=len(candidates),
            agent_calls_count=agent_calls,
            elapsed_seconds=elapsed,
            picks_file_path=str(out_path),
        )

        # 7. Phase 2: 推送到 Social Media（可选）
        if self.sma_account_id:
            self._dispatch_to_sma(picks)

        return picks

    def _archive_to_central_brain(
        self,
        picks: DailyPicks,
        candidates_count: int,
        agent_calls_count: int,
        elapsed_seconds: float,
        picks_file_path: str,
    ) -> None:
        """Persist this day's pick to the central_brain.db observability layer.

        Failures here must not break the pipeline.
        """
        try:
            from central_brain import get_central_brain

            brain = get_central_brain()

            # Aggregate today's LLM cost from the llm_calls table if anything
            # was recorded during the run; otherwise fall back to 0.
            today_iso = picks.pick_date.isoformat()
            cost_summary = brain.store.llm_cost_summary(since=f"{today_iso}T00:00:00")

            brain.store.save_daily_pick(
                pick_date=today_iso,
                is_mock_data=picks.is_mock_data,
                hot_sectors=picks.hot_sectors,
                aggressive=[asdict(s) for s in picks.aggressive],
                stable=[asdict(s) for s in picks.stable],
                candidates_count=candidates_count,
                agent_calls_count=agent_calls_count,
                total_llm_cost_usd=cost_summary.get("total_cost_usd", 0.0),
                elapsed_seconds=elapsed_seconds,
                picks_file_path=picks_file_path,
            )
            logger.info("daily pick archived to central_brain: %s", today_iso)
        except Exception as e:  # noqa: BLE001
            logger.warning("central_brain archive failed: %s", e)

    def _dispatch_to_sma(self, picks: DailyPicks) -> None:
        """组装 topic 并 HTTP 推送到 SMA。失败不阻塞主流程。"""
        try:
            # 延迟导入：dispatcher 仅在配置了 sma_account_id 时才需要
            from social_media_dispatcher import SmaClient, TopicRouter
        except Exception as e:  # noqa: BLE001
            logger.warning("social_media_dispatcher unavailable: %s", e)
            return

        try:
            payload = TopicRouter().from_daily_picks(
                picks, account_id=self.sma_account_id  # type: ignore[arg-type]
            )
            client = SmaClient(base_url=self.sma_base_url)
            result = client.dispatch(payload)
            if result.success:
                logger.info(
                    "SMA dispatch OK: account=%s task_id=%s",
                    self.sma_account_id, result.sma_task_id,
                )
                self._record_social_post(picks, payload, result)
            else:
                logger.warning("SMA dispatch failed: %s", result.error)
        except Exception as e:  # noqa: BLE001
            logger.exception("SMA dispatch raised: %s", e)

    def _record_social_post(self, picks: DailyPicks, payload, result) -> None:
        """Best-effort central_brain bookkeeping for a dispatched SMA task."""
        if not result.sma_task_id:
            return
        try:
            from central_brain import get_central_brain

            symbols = [s.symbol for s in picks.aggressive + picks.stable]
            topic = payload.description[:120] if getattr(payload, "description", None) else None

            get_central_brain().store.record_social_post(
                sma_task_id=result.sma_task_id,
                account_id=self.sma_account_id or "unknown",
                source_pick_date=picks.pick_date.isoformat(),
                source_symbols=symbols,
                topic=topic,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("central_brain social_post record failed: %s", e)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _to_recommend(
        self, stock: StockQuote, score: float, matched: list[str]
    ) -> RecommendedStock:
        return RecommendedStock(
            symbol=stock.symbol,
            name=stock.name,
            price=stock.price,
            change_pct=stock.change_pct,
            industry=stock.industry,
            rule_score=score,
            matched_rules=matched,
        )

    def _enrich_with_agent(self, stock: RecommendedStock) -> None:
        if not self.trading_agent_url:
            return
        try:
            # 真实 TradingAgents propagate 一轮约 5-8 分钟（12+ 次 LLM 调用）
            # 走缓存则毫秒级；给 15 分钟上限覆盖首次慢调用
            resp = httpx.post(
                f"{self.trading_agent_url}/analyze",
                json={"symbol": stock.symbol, "depth": "deep",
                      "requested_by": "daily_scan"},
                timeout=900,
            )
            if resp.status_code != 200:
                logger.warning("agent %s -> %s", stock.symbol, resp.status_code)
                return
            data = resp.json()
            stock.agent_decision = data["report"]["final_decision"]
            stock.agent_confidence = data["report"]["confidence"]
            stock.agent_summary = data["report"]["summary"]
        except Exception as e:  # noqa: BLE001
            logger.warning("agent call failed for %s: %s", stock.symbol, e)

    def _traders_recommend(
        self, candidates: list[RecommendedStock]
    ) -> tuple[list[RecommendedStock], list[RecommendedStock]]:
        """交易员综合判断（FR-1.4）。

        简化逻辑：
        - 激进：rule_score 高 + agent BUY + change_pct 正
        - 稳健：rule_score 中上 + agent BUY/HOLD + 已有主力流入

        每类最多 3 只。Phase 2 接入持仓集中度判断。
        """
        with_agent = [c for c in candidates if c.agent_decision is not None]

        # 激进
        aggressive_pool = [
            c for c in with_agent
            if c.agent_decision == "BUY" and c.change_pct > 0
        ]
        aggressive_pool.sort(
            key=lambda c: (c.rule_score, c.agent_confidence or 0, c.change_pct),
            reverse=True,
        )
        aggressive = aggressive_pool[:3]
        for c in aggressive:
            c.bucket = "aggressive"
            c.reasoning = (
                f"规则得分 {c.rule_score:.1f} + Agent BUY"
                f"(置信 {c.agent_confidence:.0%}) + 当日 +{c.change_pct:.1f}%"
            )

        # 稳健（排除已选入激进的）
        chosen = {c.symbol for c in aggressive}
        stable_pool = [
            c for c in with_agent
            if c.symbol not in chosen and c.agent_decision in ("BUY", "HOLD")
        ]
        stable_pool.sort(
            key=lambda c: (c.agent_confidence or 0, c.rule_score),
            reverse=True,
        )
        stable = stable_pool[:3]
        for c in stable:
            c.bucket = "stable"
            c.reasoning = (
                f"Agent {c.agent_decision} 置信 {c.agent_confidence:.0%}，"
                f"规则得分 {c.rule_score:.1f}"
            )

        return aggressive, stable
