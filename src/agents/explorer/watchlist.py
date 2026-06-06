"""WatchlistManager — 自选股池跟踪 Agent。

职责：
1. 每日从 Explorer 选股结果中纳入新候选 (尚未建仓、分数够高)
2. 每日检查 watchlist 内所有标的的价格变动
3. 判断是否触发入场条件 → 标记 triggered → 供 Strategist 优先处理
4. 自动剔除过期/失效标的 (跟踪超过 N 天未触发、基本面恶化等)

调度集成：
- 由 scheduler 每日 15:40 调用 `run_watchlist_check()`
- 也可在 LangGraph Explorer 节点中嵌入

入场条件语法（简化规则，后续可升级为 LLM 判断）：
- price_below:XX — 股价跌至 XX 以下
- change_above:XX — 当日涨幅超过 XX%
- volume_above:XX — 当日量比超过 XX
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from src.central_brain import get_central_brain
from src.infra.logger import get_agent_logger
from src.stock_analyzer.data_source import AkshareClient

logger = logging.getLogger(__name__)

# 跟踪超过此天数未触发则自动移除
_MAX_WATCH_DAYS = 20
# Explorer 规则分数阈值（高于此才加入 watchlist）
_MIN_RULE_SCORE = 3.5
# watchlist 最大容量
_MAX_WATCHLIST_SIZE = 30


class WatchlistManager:
    """自选股池管理器。"""

    def __init__(self, session_id: str = "watchlist") -> None:
        self.session_id = session_id
        self.logger = get_agent_logger("watchlist", session_id)
        self.brain = get_central_brain()
        self.akshare = AkshareClient(allow_mock_fallback=True)

    # ------------------------------------------------------------------
    # 纳入新候选 (由 Explorer 调用或独立跑)
    # ------------------------------------------------------------------

    def ingest_candidates(self, candidates: list[dict]) -> int:
        """从候选票中筛选高分标的加入 watchlist，返回新增数量。

        Args:
            candidates: StockCandidate dicts (含 symbol, name, sector, qlib_score, ...)
        """
        store = self.brain.store
        existing = set(store.get_watchlist_symbols())
        # 排除已持仓
        open_positions = store.get_watchlist(status="all")  # 简单查
        positioned = set()
        try:
            conn = store._conn()
            rows = conn.execute(
                "SELECT symbol FROM positions WHERE status = 'open'"
            ).fetchall()
            positioned = {r["symbol"] for r in rows}
        except Exception:  # noqa: BLE001
            pass

        added = 0
        for c in candidates:
            symbol = c.get("symbol", "")
            score = c.get("qlib_score", 0) or c.get("rule_score", 0)
            if not symbol or symbol in existing or symbol in positioned:
                continue
            if score < _MIN_RULE_SCORE:
                continue
            if len(existing) + added >= _MAX_WATCHLIST_SIZE:
                break

            # 构造入场条件（简单规则：低于当前价 3% 买入）
            price = c.get("kline_summary", {}).get("current_price") or c.get("price", 0)
            entry_price = round(price * 0.97, 2) if price > 0 else None
            entry_condition = f"price_below:{entry_price}" if entry_price else ""

            item = {
                "watch_id": f"W-{uuid.uuid4().hex[:8].upper()}",
                "symbol": symbol,
                "name": c.get("name", ""),
                "sector": c.get("sector", ""),
                "status": "watching",
                "thesis": "; ".join(c.get("hot_reason", [])),
                "entry_condition": entry_condition,
                "target_price": round(price * 1.12, 2) if price > 0 else None,
                "stop_loss": round(price * 0.92, 2) if price > 0 else None,
                "strategy_id": "",
                "source": "explorer_auto",
                "added_at": datetime.now().isoformat(),
                "last_price": price if price > 0 else None,
            }
            store.add_to_watchlist(item)
            added += 1

        self.logger.info("Watchlist 纳入 %d 只新候选 (existing=%d)", added, len(existing))
        return added

    # ------------------------------------------------------------------
    # 每日检查
    # ------------------------------------------------------------------

    def daily_check(self) -> dict[str, Any]:
        """对 watchlist 内所有 watching 标的做每日检查。

        Returns:
            {"checked": N, "triggered": N, "removed": N}
        """
        store = self.brain.store
        items = store.get_watchlist(status="watching")
        if not items:
            self.logger.info("Watchlist 为空，跳过检查")
            return {"checked": 0, "triggered": 0, "removed": 0}

        # 批量拉取行情
        snapshot = self.akshare.fetch_snapshot()
        price_map: dict[str, tuple[float, float]] = {}  # symbol → (price, change_pct)
        for stock in snapshot.stocks:
            price_map[stock.symbol] = (stock.price, stock.change_pct)

        triggered = 0
        removed = 0

        for item in items:
            symbol = item["symbol"]
            watch_id = item["watch_id"]
            days = item.get("days_watched", 0) or 0

            # 价格更新
            if symbol in price_map:
                price, change_pct = price_map[symbol]
                store.update_watchlist_check(watch_id, price, change_pct)
            else:
                price = item.get("last_price") or 0
                change_pct = 0

            # 检查入场条件
            if price > 0 and self._check_trigger(item, price, change_pct):
                store.trigger_watchlist_item(watch_id)
                triggered += 1
                self.logger.info(
                    "Watchlist TRIGGERED: %s %s @ %.2f (条件: %s)",
                    symbol, item.get("name"), price, item.get("entry_condition"),
                )
                self.brain.log_agent_event(
                    self.session_id, "watchlist", "triggered",
                    {"symbol": symbol, "name": item.get("name"), "price": price},
                )
                continue

            # 自动剔除
            if days + 1 > _MAX_WATCH_DAYS:
                store.remove_from_watchlist(watch_id, reason=f"超过{_MAX_WATCH_DAYS}天未触发")
                removed += 1
                self.logger.info("Watchlist REMOVED (过期): %s", symbol)

        self.logger.info(
            "Watchlist 每日检查完成: checked=%d triggered=%d removed=%d",
            len(items), triggered, removed,
        )
        return {"checked": len(items), "triggered": triggered, "removed": removed}

    # ------------------------------------------------------------------
    # 入场条件判断
    # ------------------------------------------------------------------

    def _check_trigger(self, item: dict, price: float, change_pct: float) -> bool:
        """解析 entry_condition 并判断是否满足。"""
        cond = item.get("entry_condition", "")
        if not cond:
            return False

        parts = cond.split(";")
        for part in parts:
            part = part.strip()
            if ":" not in part:
                continue
            rule, val_str = part.split(":", 1)
            try:
                val = float(val_str)
            except ValueError:
                continue

            if rule == "price_below" and price <= val:
                return True
            elif rule == "price_above" and price >= val:
                return True
            elif rule == "change_above" and change_pct >= val:
                return True
            elif rule == "change_below" and change_pct <= val:
                return True

        return False

    # ------------------------------------------------------------------
    # 供 Strategist 使用：获取触发的标的
    # ------------------------------------------------------------------

    def get_triggered_stocks(self) -> list[dict]:
        """获取所有已触发入场条件的 watchlist 标的。"""
        return self.brain.store.get_watchlist(status="triggered")


# =============================================================================
# 调度入口
# =============================================================================

def run_watchlist_check() -> dict[str, Any]:
    """调度器调用入口：纳入 + 检查一步到位。"""
    mgr = WatchlistManager()
    result = mgr.daily_check()
    return result


def run_watchlist_ingest(candidates: list[dict]) -> int:
    """Explorer 完成后调用：将高分候选纳入 watchlist。"""
    mgr = WatchlistManager()
    return mgr.ingest_candidates(candidates)
