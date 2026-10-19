"""短线高弹性动态自选股池 (Short-Term Momentum Watchlist Manager)。

核心设计：
1. 动态三池结构中的「今日复盘备选池」与「强势趋势观察池」（行云科技式二次跟踪）。
2. 高周转：池子保持 10 ~ 20 只高弹性标的（创业板/科创板/北交所/中小板）。
3. 严格冷酷踢出：
   - 破位踢出：单日暴跌 > 6% 或形态破位；
   - 流动性枯竭：成交额萎缩至 1.5 亿以下；
   - 弹性枯竭：振幅连续低于 3%；
   - 时间止损：入池超过 4 天未触发入场直接踢出，不养闲股。
4. 生产写入遵循统一中央库 central_brain.db 的 watchlist 表。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from src.central_brain import get_central_brain
from src.stock_analyzer.detectors.ultra_short_momentum import (
    STRATEGY_ID,
    _ALLOWED_PREFIXES,
    _CONT_RET5,
    _CONT_RET20,
)

logger = logging.getLogger(__name__)

# 自选股池容量控制
TARGET_POOL_MIN = 10
TARGET_POOL_MAX = 20

# 严格剔除门槛
MAX_WATCH_DAYS = 4           # 超过 4 天未触发入场则时间止损踢出
MIN_TURNOVER = 1.5e8         # 成交额低于 1.5 亿视为流动性枯竭
MIN_AMPLITUDE = 0.03         # 日内真实振幅低于 3% 视为丧失高弹性
MAX_SINGLE_DAY_DROP = -0.06  # 单日跌幅超 -6% 视为形态破位

# 盘后补票筛选（全市场快照维度）
FEED_MIN_TURNOVER = 3e8        # 当日成交额 >= 3 亿
FEED_MIN_TURNOVER_RATE = 8.0   # 换手率 >= 8%（高弹性代理指标）
FEED_FALLBACK_TURNOVER = 5e8   # 换手率缺失（新浪源）时成交额门槛提高至 5 亿
FEED_MIN_CHANGE_PCT = -4.0     # 当日跌幅下限（不接崩坏飞刀）
FEED_MAX_CHANGE_PCT = 11.0     # 当日涨幅上限（不追已涨停/过热）
FEED_TOP_N = 40                # 每次最多考察的活跃标的数


def _is_allowed_symbol(symbol: str) -> bool:
    """仅允许高弹性板块：创业板(300)、科创板(688)、北交所(920/834/430)、深市题材(000/001/002)。"""
    return bool(symbol) and symbol.startswith(_ALLOWED_PREFIXES)


def _is_clean_name(name: str) -> bool:
    """过滤 ST 和退市整理标的。"""
    up = (name or "").upper()
    return "ST" not in up and "退" not in up


class ShortTermWatchlistManager:
    """短线高弹性动态自选股池管理器。"""

    def __init__(self, brain=None) -> None:
        self.brain = brain or get_central_brain()

    def get_active_pool(self) -> list[dict]:
        """获取当前处于 watching 状态的短线自选股列表。"""
        store = self.brain.store
        return store.get_watchlist(status="watching", strategy_id=STRATEGY_ID)

    def get_active_symbols(self) -> list[str]:
        """获取当前 watching 状态的短线 symbol 列表。"""
        store = self.brain.store
        return store.get_watchlist_symbols(strategy_id=STRATEGY_ID)

    def add_candidate(
        self,
        symbol: str,
        name: str = "",
        sector: str = "",
        price: float = 0.0,
        thesis: str = "",
        source: str = "tail_hunt_candidate",
        target_price: float | None = None,
        stop_loss: float | None = None,
    ) -> bool:
        """纳入单只高弹性候选股到短线自选池。

        Returns:
            True 表示成功纳入，False 表示被过滤或已存在。
        """
        if not _is_allowed_symbol(symbol) or not _is_clean_name(name):
            return False

        store = self.brain.store
        existing_symbols = set(self.get_active_symbols())
        if symbol in existing_symbols:
            return False

        # 检查是否已在当前开仓持仓中
        try:
            open_positions = store.list_open_positions(persona_id="ultra_short_momentum_v1")
            if any(p.get("symbol") == symbol for p in open_positions):
                return False
        except Exception:  # noqa: BLE001
            pass

        # 容量控制：若已达上限，先不加（或由清洗后再加）
        if len(existing_symbols) >= TARGET_POOL_MAX:
            logger.info("短线自选股池已满 (%d/%d)，跳过 %s", len(existing_symbols), TARGET_POOL_MAX, symbol)
            return False

        watch_id = f"W-ST-{uuid.uuid4().hex[:8].upper()}"
        calc_target = target_price or (round(price * 1.08, 2) if price > 0 else None)
        calc_stop = stop_loss or (round(price * 0.975, 2) if price > 0 else None)

        item = {
            "watch_id": watch_id,
            "symbol": symbol,
            "name": name,
            "sector": sector,
            "status": "watching",
            "thesis": thesis or "高弹性隔夜动量候选",
            "entry_condition": "momentum_continuation;strong_pullback",
            "target_price": calc_target,
            "stop_loss": calc_stop,
            "strategy_id": STRATEGY_ID,
            "source": source,
            "added_at": datetime.now().isoformat(),
            "last_price": price if price > 0 else None,
            "days_watched": 0,
            "notes": f"source={source}",
        }
        store.add_to_watchlist(item)
        logger.info("[Watchlist+] 新增短线自选股: %s %s (source=%s, price=%.2f)", symbol, name, source, price)
        return True

    def add_rebound_watch(
        self,
        symbol: str,
        name: str = "",
        sector: str = "",
        last_price: float = 0.0,
        reason: str = "平仓后中期趋势未破，保留观察二次狙击",
    ) -> bool:
        """复刻「行云科技」逻辑：持仓平仓后若中期趋势健康，自动回流自选股。"""
        return self.add_candidate(
            symbol=symbol,
            name=name,
            sector=sector,
            price=last_price,
            thesis=reason,
            source="rebound_watch",
        )

    def refresh_from_candidates(self, candidates: list[dict]) -> int:
        """从全市场/扫描器候选票中批量筛选补齐短线自选池。

        Args:
            candidates: 包含 symbol, name, price, turnover, amplitude 等信息的字典列表

        Returns:
            实际新增入池的数量
        """
        current_pool = self.get_active_pool()
        needed = TARGET_POOL_MAX - len(current_pool)
        if needed <= 0:
            return 0

        # 排序：优先按成交额或涨跌幅排序
        sorted_candidates = sorted(
            candidates,
            key=lambda c: (float(c.get("turnover", 0) or 0), float(c.get("change_pct", 0) or 0)),
            reverse=True,
        )

        added = 0
        for c in sorted_candidates:
            if added >= needed:
                break
            symbol = c.get("symbol", "")
            name = c.get("name", "")
            turnover = float(c.get("turnover", 0) or 0)
            amplitude = float(c.get("amplitude", 0) or 0)

            # 硬过滤：成交额 >= 3亿，振幅 >= 5%
            if turnover > 0 and turnover < 3e8:
                continue
            if amplitude > 0 and amplitude < 0.05:
                continue

            price = float(c.get("current_price", 0) or c.get("price", 0) or 0)
            ok = self.add_candidate(
                symbol=symbol,
                name=name,
                sector=c.get("sector", ""),
                price=price,
                thesis="; ".join(c.get("hot_reason", [])) if isinstance(c.get("hot_reason"), list) else str(c.get("thesis", "")),
                source="tail_hunt_candidate",
            )
            if ok:
                added += 1

        logger.info("短线自选股池刷新: 补充入池 %d 只，当前在池 %d 只", added, len(current_pool) + added)
        return added

    def _fetch_kline_metrics(self, client, symbol: str) -> dict[str, float | None]:
        """从行情库读取最近日线（昨日及之前数据已在库内），计算振幅与 5日/20日涨幅。"""
        try:
            bars = client.fetch_kline(symbol, days=25)
        except Exception:  # noqa: BLE001
            return {"amplitude": 0.0, "ret5": None, "ret20": None}
        if not bars or getattr(bars[-1], "is_mock", False):
            return {"amplitude": 0.0, "ret5": None, "ret20": None}
        last = bars[-1]
        prev_close = bars[-2].close if len(bars) >= 2 and bars[-2].close > 0 else None
        if prev_close is None and last.close > 0 and last.change_pct > -99:
            prev_close = last.close / (1 + last.change_pct / 100)
        amplitude = (
            max(0.0, (last.high - last.low) / prev_close)
            if prev_close and prev_close > 0
            else 0.0
        )
        ret5 = (
            (last.close / bars[-6].close - 1.0)
            if len(bars) >= 6 and bars[-6].close > 0
            else None
        )
        ret20 = (
            (last.close / bars[-21].close - 1.0)
            if len(bars) >= 21 and bars[-21].close > 0
            else None
        )
        return {"amplitude": amplitude, "ret5": ret5, "ret20": ret20}

    def _fetch_today_amplitude(self, client, symbol: str) -> float:
        """拉最近日线计算当日真实振幅 (high-low)/前收；失败返回 0（跳过弹性闸门）。"""
        metrics = self._fetch_kline_metrics(client, symbol)
        return float(metrics.get("amplitude") or 0.0)

    def run_daily_maintenance(self, snapshot=None, client=None) -> dict[str, Any]:
        """盘后维护闭环：真实行情清洗存量 + 全市场快照补票入池。

        Args:
            snapshot: 可选注入的 MarketSnapshot（测试用）；None 则实时拉取。
            client: 可选注入的行情客户端（测试用）；None 则新建 AkshareClient。

        Returns:
            {"data_ok": bool, "audited": dict, "added": int, "pool_size": int}
        """
        from src.stock_analyzer.data_source.akshare_client import AkshareClient

        if client is None:
            client = AkshareClient(allow_mock_fallback=False)
        if snapshot is None:
            try:
                snapshot = client.fetch_snapshot()
            except Exception as e:  # noqa: BLE001
                logger.warning("短线池维护：快照拉取失败，跳过行情维度清洗与补票: %s", e)
                snapshot = None

        data_ok = bool(snapshot) and not getattr(snapshot, "is_mock", True)
        quotes_map: dict[str, dict[str, Any]] = {}
        feed_candidates: list[dict[str, Any]] = []

        if data_ok:
            by_symbol = {s.symbol: s for s in snapshot.stocks}
            for item in self.get_active_pool():
                q = by_symbol.get(item["symbol"])
                if not q or q.price <= 0:
                    continue
                metrics = self._fetch_kline_metrics(client, item["symbol"])
                q_high = float(getattr(q, "high", 0) or 0)
                q_low = float(getattr(q, "low", 0) or 0)
                q_prev = float(getattr(q, "prev_close", 0) or 0)
                snap_amp = (
                    max(0.0, (q_high - q_low) / q_prev)
                    if q_high > 0 and q_low > 0 and q_prev > 0
                    else 0.0
                )
                quotes_map[item["symbol"]] = {
                    "price": q.price,
                    "turnover": q.turnover,
                    "change_pct": q.change_pct,
                    "amplitude": float(metrics.get("amplitude") or 0.0) or snap_amp,
                    "ret5": metrics.get("ret5"),
                    "ret20": metrics.get("ret20"),
                }
            movers = [
                s for s in snapshot.stocks
                if _is_allowed_symbol(s.symbol)
                and _is_clean_name(s.name)
                and s.price > 0
                and FEED_MIN_CHANGE_PCT <= s.change_pct <= FEED_MAX_CHANGE_PCT
                and (
                    # 换手率可用时：成交 >= 3 亿且换手 >= 8%
                    (s.turnover_rate > 0 and s.turnover >= FEED_MIN_TURNOVER
                     and s.turnover_rate >= FEED_MIN_TURNOVER_RATE)
                    # 换手率缺失（新浪等降级源）：成交额门槛提高至 5 亿
                    or (s.turnover_rate <= 0 and s.turnover >= FEED_FALLBACK_TURNOVER)
                )
            ]
            movers.sort(key=lambda s: s.turnover, reverse=True)
            for s in movers[:FEED_TOP_N]:
                metrics = self._fetch_kline_metrics(client, s.symbol)
                ret5 = metrics.get("ret5")
                ret20 = metrics.get("ret20")
                # 若行情库已有完整历史日线，强制校验 5日/20日多日动量，拒绝阴跌大票入池
                if ret20 is not None and ret5 is not None:
                    if ret20 < _CONT_RET20 or (ret5 < _CONT_RET5 and ret20 < 0.08):
                        continue
                rate_txt = (
                    f"换手{s.turnover_rate:.1f}%"
                    if s.turnover_rate > 0 else "换手率缺失"
                )
                feed_candidates.append({
                    "symbol": s.symbol,
                    "name": s.name,
                    "sector": s.industry or "",
                    "current_price": s.price,
                    "turnover": s.turnover,
                    "amplitude": float(metrics.get("amplitude") or 0.0),
                    "change_pct": s.change_pct,
                    "thesis": (
                        f"盘后复盘入池: {rate_txt} "
                        f"成交{s.turnover / 1e8:.1f}亿 涨幅{s.change_pct:+.2f}%"
                    ),
                })

        audited = self.audit_and_clean(quotes_map)
        added = self.refresh_from_candidates(feed_candidates)
        pool_size = len(self.get_active_symbols())
        logger.info(
            "短线池盘后维护完成: data_ok=%s 清洗=%s 补票=%d 在池=%d",
            data_ok, audited, added, pool_size,
        )
        return {"data_ok": data_ok, "audited": audited, "added": added, "pool_size": pool_size}

    def audit_and_clean(self, quotes_map: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """盘后/盘前自选股清洗：冷酷剔除破位、缩量、弹性枯竭或超期标的。

        Args:
            quotes_map: 可选的行情字典，{symbol: {"price": float, "turnover": float, "amplitude": float, "change_pct": float}}

        Returns:
            {"checked": int, "removed": int, "reasons": list[dict]}
        """
        store = self.brain.store
        items = self.get_active_pool()
        if not items:
            return {"checked": 0, "removed": 0, "reasons": []}

        quotes_map = quotes_map or {}
        removed_count = 0
        details = []

        for item in items:
            watch_id = item["watch_id"]
            symbol = item["symbol"]
            name = item.get("name", symbol)
            days = int(item.get("days_watched", 0) or 0)

            remove_reason = None

            # 1. 时间止损踢出（冷板凳规则：超期未触发）
            if days >= MAX_WATCH_DAYS:
                remove_reason = f"入池 {days} 天超过 {MAX_WATCH_DAYS} 天未触发入场，时间止损踢出"

            # 2. 行情维度踢出（如果有最新行情数据）
            elif symbol in quotes_map:
                quote = quotes_map[symbol]
                price = float(quote.get("price", 0) or 0)
                turnover = float(quote.get("turnover", 0) or 0)
                amplitude = float(quote.get("amplitude", 0) or 0)
                change_pct = float(quote.get("change_pct", 0) or 0)

                # 更新检查数据
                store.update_watchlist_check(watch_id, price, change_pct)

                # 破位踢出：单日暴跌超 -6% (change_pct 单位统一为百分比，如 -6.0 表示 -6%)
                if change_pct <= (MAX_SINGLE_DAY_DROP * 100):
                    remove_reason = f"单日大幅下挫 {change_pct:.2f}% (突破 -6% 止损线)，形态破位踢出"
                # 流动性枯竭踢出：成交额低于 1.5 亿
                elif 0 < turnover < MIN_TURNOVER:
                    remove_reason = f"当日成交额仅 {turnover/1e8:.2f} 亿 (低于 1.5 亿流动性红线)，流动性枯竭踢出"
                # 弹性枯竭踢出：真实振幅低于 3%
                elif 0 < amplitude < MIN_AMPLITUDE:
                    remove_reason = f"日内振幅仅 {amplitude:.1%} (低于 3% 弹性红线)，丧失波动红利踢出"
                # 中期动量丧失踢出：20 日涨幅转负
                elif quote.get("ret20") is not None and float(quote["ret20"]) < 0.0:
                    remove_reason = f"20日涨幅转负 ({float(quote['ret20']):.1%})，中期动量丧失踢出"

            else:
                # 无外部行情，递增 days_watched
                last_price = float(item.get("last_price") or 0)
                store.update_watchlist_check(watch_id, last_price, 0.0)

            if remove_reason:
                store.remove_from_watchlist(watch_id, reason=remove_reason)
                removed_count += 1
                details.append({"symbol": symbol, "name": name, "reason": remove_reason})
                logger.info("[Watchlist-] 踢出自选股 %s %s: %s", symbol, name, remove_reason)

        logger.info(
            "短线自选池审查完成: 检查 %d 只, 踢出 %d 只, 剩余 %d 只",
            len(items), removed_count, len(items) - removed_count,
        )
        return {
            "checked": len(items),
            "removed": removed_count,
            "remaining": len(items) - removed_count,
            "reasons": details,
        }
