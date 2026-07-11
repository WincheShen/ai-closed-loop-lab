"""Explorer Scanner — 探索者核心实现。

职责：
1. 每日收盘后抓取全市场行情快照（AKShare 真实数据，失败时降级 mock）
2. 检测热点板块 Top 5
3. 运行规则引擎筛选候选票 → Top 30
4. 经验过滤：黑名单/冷却期/历史降权（ExperienceLayer）
5. 拉取候选票近期 K 线，生成走势摘要供 Strategist 使用
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.agents.cio.trading_persona import load_persona_by_id
from src.central_brain import get_central_brain
from src.graph.state import StockCandidate, TradingState
from src.infra.logger import get_agent_logger
from src.stock_analyzer.data_source import AkshareClient, HotSectorDetector
from src.stock_analyzer.rules import RuleEngine, load_rules_from_yaml, Rule
from src.stock_analyzer.rules.rule_engine import get_registered

logger = get_agent_logger("explorer", "init")

_RULES_YAML = Path("config/rules.yaml")
_KLINE_ENRICH_LIMIT = 10

# persona fundamental_filters indicator → builtin rule_id 映射
_INDICATOR_TO_RULE: dict[str, tuple[str, str]] = {
    "roe": ("high_roe", "min_roe"),
    "debt_to_equity": ("low_debt", "max_de"),
    "dividend_yield": ("high_dividend", "min_yield"),
    "free_cash_flow_yield": ("high_fcf_yield", "min_fcf"),
    "pe": ("value_pe", "pe_max"),
}

# 规则 ID → 策略 ID 映射（用于查询 StrategyLedger 调整权重）
_RULE_TO_STRATEGY: dict[str, str] = {
    "in_hot_sector": "热点板块前排回踩",
    "volume_breakout": "放量突破",
    "strong_turnover": "放量突破",
    "main_fund_inflow": "热点板块前排回踩",
}


class ExplorerScanner:
    """探索者扫描器 — AKShare 行情 + 规则引擎 + 热点检测（多人格支持）。"""

    def __init__(self, session_id: str, persona_id: str | None = None, market_regime: dict | None = None) -> None:
        self.session_id = session_id
        self.persona_id = persona_id
        self.logger = get_agent_logger("explorer", session_id)
        self.brain = get_central_brain()
        self.akshare = AkshareClient(allow_mock_fallback=True)
        self.hot_detector = HotSectorDetector()
        self._snapshot = None
        self._hot_names: list[str] = []
        self._persona = load_persona_by_id(persona_id) if persona_id else None
        self._kline_days = self._get_kline_lookback_days()
        self._market_regime = market_regime or {}

    def scan_market(self, date_str: str | None = None, snapshot: MarketSnapshot | None = None, hot_sectors: list[str] | None = None) -> list[StockCandidate]:
        """全市场扫描 → 热点检测 → 规则引擎 → 候选票列表。

        Args:
            date_str: 日期字符串（可选，用于日志）
            snapshot: 预先拉取的 MarketSnapshot（可选，传入则复用，避免重复拉取）
            hot_sectors: 预先检测的热点板块（可选，传入则复用，避免重复检测）
        """
        self.logger.info("开始全市场扫描")

        # 1. 全市场快照（优先使用传入的 snapshot，否则真实 AKShare 数据，失败降级 mock）
        if snapshot is not None:
            self._snapshot = snapshot
            self.logger.info(
                "复用传入快照: date=%s mock=%s stocks=%d sectors=%d",
                self._snapshot.snapshot_date, self._snapshot.is_mock,
                len(self._snapshot.stocks), len(self._snapshot.sectors),
            )
        else:
            self._snapshot = self.akshare.fetch_snapshot()
            self.logger.info(
                "行情快照: date=%s mock=%s stocks=%d sectors=%d",
                self._snapshot.snapshot_date, self._snapshot.is_mock,
                len(self._snapshot.stocks), len(self._snapshot.sectors),
            )

        # 2. 热点板块 Top 5（优先使用传入的 hot_sectors，否则独立检测）
        if hot_sectors is not None:
            self._hot_names = hot_sectors
            self.logger.info("复用传入热点板块: %s", self._hot_names)
        else:
            hot_results = self.hot_detector.detect(self._snapshot, top_k=5)
            self._hot_names = [h.sector.name for h in hot_results]
            self.logger.info("热点板块: %s", self._hot_names)

        # 3. 价值投资人格：使用 ValueStockPool 作为主要候选池
        if self._is_value_persona():
            self._snapshot = self._build_value_universe(self._snapshot)
        elif self._needs_fundamental_data():
            try:
                from src.stock_analyzer.data_source import FundamentalClient
                fc = FundamentalClient()
                fc.enrich_quotes(self._snapshot.stocks)
                self.logger.info("基本面数据已填充 (persona=%s)", self.persona_id)
            except Exception:
                self.logger.warning("基本面数据填充失败 (不影响主流程)", exc_info=True)

        # 4. 规则引擎筛选（支持人格级规则覆盖 + 经验驱动权重调整）
        # min_score 从 2.0 提升到 2.5 — 提高入场质量，宁缺勿滥
        # (依据: 历史数据 53% 退出是止损，说明入场质量偏弱)
        rules = self._build_rules_for_persona()
        engine = RuleEngine(rules)
        results = engine.filter_and_rank(
            self._snapshot.stocks, min_score=2.5, top_k=20,
        )
        self.logger.info("规则引擎筛选: %d 只通过 (persona=%s)", len(results), self.persona_id or "default")

        # 5. 转化为 StockCandidate（前 N 只附带 K 线摘要）
        max_score = results[0].score if results else 1.0
        candidates: list[StockCandidate] = []
        for idx, r in enumerate(results):
            stock = r.stock
            kline = (
                self._build_kline_summary(stock.symbol, stock.price, days=self._kline_days)
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
                    "roe": stock.roe,
                    "debt_to_equity": stock.debt_to_equity,
                    "dividend_yield": stock.dividend_yield,
                    "fcf_yield": stock.fcf_yield,
                },
                "fund_flow": {
                    "main_net_inflow": stock.main_fund_net_inflow,
                    "turnover": stock.turnover,
                    "turnover_rate": stock.turnover_rate,
                },
                "dragon_tiger": None,
            })

        # 6. 经验过滤：黑名单 / 冷却期 / 历史胜率降权
        candidates = self._apply_experience_filter(candidates)

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

    def _get_kline_lookback_days(self) -> int:
        """根据人格配置返回 K 线回看天数。"""
        if self._persona and self._persona.stock_selection_rules:
            return self._persona.stock_selection_rules.get("kline_lookback_days", 20)
        return 20

    def _apply_experience_filter(self, candidates: list[StockCandidate]) -> list[StockCandidate]:
        """对候选票应用经验过滤（黑名单/冷却期/历史降权）。

        这是闭环的关键环节：交易归因 → ExperienceLayer → Explorer 选股。
        """
        try:
            from src.experience_layer import get_experience
            exp = get_experience()
            blacklist = exp.blacklist_for(persona_id=self.persona_id)
            regime = self._market_regime.get("regime")
            filtered = blacklist.filter_candidates(candidates, regime=regime)
            removed = len(candidates) - len(filtered)
            if removed > 0:
                self.logger.info(
                    "经验过滤: %d → %d 只候选 (移除 %d 只)",
                    len(candidates), len(filtered), removed,
                )
            return filtered
        except Exception:
            self.logger.warning("经验过滤失败 (降级: 不过滤)", exc_info=True)
            return candidates

    def _build_rules_for_persona(self) -> list[Rule]:
        """根据人格配置构建选股规则列表。

        对价值投资人格：
        - 禁用热点板块、量价类规则
        - 从 persona.fundamental_filters 动态生成基本面规则

        经验驱动权重调整：
        - 查询 StrategyLedger 获取策略在当前 regime 下的胜率
        - 胜率高的规则权重 × 1.5~1.8，胜率低的 × 0.3~0.5
        """
        rules = load_rules_from_yaml(_RULES_YAML)

        # 更新热点板块参数
        for rule in rules:
            if rule.id == "in_hot_sector":
                rule.params = {**rule.params, "hot_sectors": self._hot_names}

        # 注入龙虎榜数据（用于 institutional_buying 规则）
        # 失败降级：拉取失败时规则自动 miss，不影响主流程
        dragon_tiger_map = self._fetch_dragon_tiger_map()
        if dragon_tiger_map:
            for rule in rules:
                if rule.id == "institutional_buying":
                    rule.params = {**rule.params, "dragon_tiger_map": dragon_tiger_map}
            self.logger.info(
                "龙虎榜数据已注入: %d 只股票，其中 %d 只有机构净买入",
                len(dragon_tiger_map),
                sum(1 for r in dragon_tiger_map.values() if r.get("institutional_net_buy_wan", 0) > 0),
            )

        if not self._persona or not self._persona.stock_selection_rules:
            return rules

        stock_rules = self._persona.stock_selection_rules

        # 如果人格不关注热点，禁用 in_hot_sector 规则
        follow_hot = stock_rules.get("follow_hot_sectors", True)
        if not follow_hot:
            for rule in rules:
                if rule.id == "in_hot_sector":
                    rule.enabled = False
            # 价值投资人格：完全禁用量价类规则（避免干扰基本面筛选）
            for rule in rules:
                if rule.id in ("volume_breakout", "strong_turnover", "main_fund_inflow"):
                    rule.enabled = False
            self.logger.info("人格 %s 禁用热点+量价规则", self.persona_id)

        # 从 persona.fundamental_filters 动态生成基本面规则
        fundamental_filters = stock_rules.get("fundamental_filters", [])
        for filt in fundamental_filters:
            indicator = filt.get("indicator", "")
            mapping = _INDICATOR_TO_RULE.get(indicator)
            if not mapping:
                continue
            rule_id, param_key = mapping
            func = get_registered(rule_id)
            if not func:
                continue

            weight = float(filt.get("weight", 1.0))
            value = filt.get("value")
            if value is None:
                continue

            params = {param_key: float(value)}
            rules.append(Rule(
                id=rule_id,
                name=f"persona_{indicator}",
                func=func,
                enabled=True,
                weight=weight,
                params=params,
            ))
            self.logger.debug(
                "人格规则: %s %s=%s weight=%.1f", rule_id, param_key, value, weight,
            )

        # 经验驱动权重调整：根据 StrategyLedger 动态调整规则权重
        rules = self._adjust_rule_weights_from_experience(rules)

        return rules

    def _fetch_dragon_tiger_map(self) -> dict[str, dict]:
        """拉取近 3 天龙虎榜数据并转为 {symbol: dict} 供规则使用。

        失败/无 akshare 时返回空 dict，规则会自动 miss，不影响主流程。
        """
        try:
            from src.stock_analyzer.data_source.dragon_tiger_client import DragonTigerClient
            client = DragonTigerClient()
            records = client.fetch_recent(days_back=3)
            return {sym: rec.as_dict() for sym, rec in records.items()}
        except Exception as e:
            self.logger.warning("龙虎榜拉取失败 (降级): %s", e)
            return {}

    def _adjust_rule_weights_from_experience(self, rules: list[Rule]) -> list[Rule]:
        """根据 StrategyLedger 的实盘数据动态调整规则权重。

        例如: 放量突破在 bear 市场胜率 0% → volume_breakout 权重 × 0.3
        """
        try:
            from src.experience_layer import get_experience
            exp = get_experience()
            regime = self._market_regime.get("regime")
            adjusted = 0
            for rule in rules:
                if not rule.enabled:
                    continue
                strategy_id = _RULE_TO_STRATEGY.get(rule.id)
                if not strategy_id:
                    continue
                multiplier = exp.get_rule_weight_multiplier(strategy_id, regime=regime)
                if multiplier != 1.0:
                    old_weight = rule.weight
                    rule.weight = round(rule.weight * multiplier, 2)
                    self.logger.info(
                        "经验调权: %s (策略=%s, regime=%s) 权重 %.1f → %.1f (×%.2f)",
                        rule.id, strategy_id, regime or "all",
                        old_weight, rule.weight, multiplier,
                    )
                    adjusted += 1
            if adjusted > 0:
                self.logger.info("经验驱动调整了 %d 条规则权重", adjusted)
        except Exception:
            self.logger.warning("经验驱动权重调整失败 (降级: 不调整)", exc_info=True)
        return rules

    def _is_value_persona(self) -> bool:
        """判断是否为价值投资人格（不追热点 + 有基本面筛选）。"""
        if not self._persona or not self._persona.stock_selection_rules:
            return False
        rules = self._persona.stock_selection_rules
        return (
            not rules.get("follow_hot_sectors", True)
            and len(rules.get("fundamental_filters", [])) >= 2
        )

    def _build_value_universe(self, full_snapshot) -> "MarketSnapshot":
        """为价值投资人格构建专用选股池。

        策略：
        1. 从 ValueStockPool 获取基本面预筛选的候选票（~150只）
        2. 用当日快照中的实时价格/成交数据更新这些票的行情字段
        3. 对候选池进行基本面数据填充（数量小，可全量覆盖）
        4. 返回一个精简的 MarketSnapshot 供规则引擎使用
        """
        from src.agents.explorer.value_pool import ValueStockPool
        from src.stock_analyzer.data_source.akshare_client import MarketSnapshot

        # 1. 获取 ValuePool 候选票
        try:
            vp = ValueStockPool()
            pool_stocks = vp.get_pool()
        except Exception:
            self.logger.warning("ValuePool 获取失败，降级使用全量快照", exc_info=True)
            return full_snapshot

        if not pool_stocks:
            self.logger.warning("ValuePool 为空，降级使用全量快照")
            return full_snapshot

        # 2. 用实时快照数据更新 pool 票的行情字段（价格/成交额/涨跌幅）
        live_map = {s.symbol: s for s in full_snapshot.stocks}
        updated_count = 0
        for ps in pool_stocks:
            live = live_map.get(ps.symbol)
            if live:
                ps.price = live.price
                ps.change_pct = live.change_pct
                ps.volume = live.volume
                ps.turnover = live.turnover
                ps.turnover_rate = live.turnover_rate
                ps.main_fund_net_inflow = live.main_fund_net_inflow
                # 保留 pool 中的基本面数据（如有），否则用 live 数据
                if ps.pe_ttm is None and live.pe_ttm is not None:
                    ps.pe_ttm = live.pe_ttm
                if ps.pb is None and live.pb is not None:
                    ps.pb = live.pb
                if ps.market_cap_yi is None and live.market_cap_yi is not None:
                    ps.market_cap_yi = live.market_cap_yi
                updated_count += 1

        self.logger.info(
            "价值投资选股池: %d 只候选, %d 只有实时行情",
            len(pool_stocks), updated_count,
        )

        # 3. 基本面数据填充（仅对 pool 票，数量可控）
        try:
            from src.stock_analyzer.data_source import FundamentalClient
            fc = FundamentalClient()
            fc.enrich_quotes(pool_stocks)
            enriched = sum(1 for s in pool_stocks if s.roe is not None)
            self.logger.info("基本面填充完成: %d/%d 有 ROE 数据", enriched, len(pool_stocks))
        except Exception:
            self.logger.warning("基本面数据填充失败 (不影响主流程)", exc_info=True)

        # 4. 构建精简的 MarketSnapshot
        return MarketSnapshot(
            snapshot_date=full_snapshot.snapshot_date,
            stocks=pool_stocks,
            sectors=full_snapshot.sectors,
            is_mock=full_snapshot.is_mock,
        )

    def _needs_fundamental_data(self) -> bool:
        """判断当前人格是否需要基本面数据。"""
        if not self._persona or not self._persona.stock_selection_rules:
            return False
        filters = self._persona.stock_selection_rules.get("fundamental_filters", [])
        has_fundamental = any(
            f.get("indicator") in ("roe", "debt_to_equity", "dividend_yield", "free_cash_flow_yield")
            for f in filters
        )
        return has_fundamental

    def _build_kline_summary(self, symbol: str, current_price: float, days: int = 20) -> dict:
        """拉取近 N 日 K 线，生成数值摘要供 Strategist LLM 分析。"""
        try:
            bars = self.akshare.fetch_kline(symbol, days=days)
            if not bars:
                return {"current_price": current_price, "trend": "no_data"}

            closes = [b.close for b in bars]
            volumes = [b.volume for b in bars]
            highs = [b.high for b in bars]
            lows = [b.low for b in bars]
            n = len(closes)

            ma5 = sum(closes[-5:]) / min(5, n) if n >= 1 else 0
            ma10 = sum(closes[-10:]) / min(10, n) if n >= 1 else 0
            ma20 = sum(closes) / n if n >= 1 else 0
            avg_vol = sum(volumes) / n if n >= 1 else 1
            latest_vol = volumes[-1] if volumes else 0

            # ATR (14-day True Range 平均) — 用于动态止损参考
            atr = 0.0
            atr_pct = 0.0
            if n >= 2:
                trs = []
                for i in range(1, n):
                    tr = max(
                        highs[i] - lows[i],
                        abs(highs[i] - closes[i - 1]),
                        abs(lows[i] - closes[i - 1]),
                    )
                    trs.append(tr)
                # 取最近 14 天（不足则全用）
                window = trs[-14:] if len(trs) >= 14 else trs
                atr = sum(window) / len(window) if window else 0.0
                atr_pct = round(atr / current_price * 100, 2) if current_price > 0 else 0.0

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
                "atr": round(atr, 2),
                "atr_pct": atr_pct,  # 波动率百分比，如 3.5 表示日均波幅 3.5%
                "suggested_stop_pct": round(min(max(atr_pct * 2, 3.0), 8.0), 2),  # 2×ATR 且限制在 3-8% 之间
            }
        except Exception as e:
            self.logger.warning("K线摘要失败 %s: %s", symbol, e)
            return {"current_price": current_price, "trend": "error"}


def run_discovery_node(state: TradingState) -> dict[str, Any]:
    """LangGraph 节点函数 — 探索者扫描。

    输入：TradingState (空或含上次状态)
    输出：{"target_stocks": [...], "hot_sectors": [...]}
    """
    from src.stock_analyzer.data_source.akshare_client import MarketSnapshot, StockQuote, SectorQuote
    from datetime import date

    session_id = state["session_id"]
    persona_id = state.get("persona_id")
    market_regime = state.get("market_regime") or {}
    scanner = ExplorerScanner(session_id, persona_id=persona_id, market_regime=market_regime)

    # 尝试从 state 中复用 MarketBrain 的快照
    market_snapshot_dict = state.get("market_snapshot")
    if market_snapshot_dict:
        # 反序列化 MarketSnapshot
        stocks = [
            StockQuote(
                symbol=s["symbol"],
                name=s["name"],
                price=s["price"],
                change_pct=s["change_pct"],
                volume=s["volume"],
                turnover=s["turnover"],
                turnover_rate=s["turnover_rate"],
                pe_ttm=s["pe_ttm"],
                pb=s["pb"],
                market_cap_yi=s["market_cap_yi"],
                industry=s["industry"],
                main_fund_net_inflow=s["main_fund_net_inflow"],
            )
            for s in market_snapshot_dict.get("stocks", [])
        ]
        sectors = [
            SectorQuote(
                name=s["name"],
                change_pct=s["change_pct"],
                turnover=s["turnover"],
                leading_stocks=s["leading_stocks"],
                main_fund_net_inflow=s["main_fund_net_inflow"],
            )
            for s in market_snapshot_dict.get("sectors", [])
        ]
        snapshot = MarketSnapshot(
            snapshot_date=date.fromisoformat(market_snapshot_dict["snapshot_date"]),
            stocks=stocks,
            sectors=sectors,
            is_mock=market_snapshot_dict["is_mock_data"],
        )
        logger.info("Explorer 复用 MarketBrain 快照: %d 只股票, %d 个板块", len(stocks), len(sectors))
    else:
        snapshot = None
        logger.info("Explorer 未找到 market_snapshot，将独立拉取快照")

    candidates = scanner.scan_market(snapshot=snapshot, hot_sectors=state.get("hot_sectors"))

    # 热点板块已经在 scan_market 中从 state 获取
    hot_sectors = scanner.fetch_hot_sectors()

    # 将高分候选纳入自选股池 (不阻塞主流程，但记录异常堆栈)
    try:
        from src.agents.explorer.watchlist import run_watchlist_ingest
        ingested = run_watchlist_ingest(candidates)
        logger.info("Watchlist 纳入 %d 只新候选", ingested)
    except Exception:
        logger.exception("Watchlist ingest 失败 (不影响主流程)")

    return {
        "target_stocks": candidates,
        "hot_sectors": hot_sectors,
        "timestamp": datetime.now().isoformat(),
        "logs": state.get("logs", []) + [
            f"[Explorer] 扫描完成: {len(candidates)} 只候选票, "
            f"热点: {', '.join(hot_sectors)}"
        ],
    }
