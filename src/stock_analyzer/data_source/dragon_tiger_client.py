"""龙虎榜数据源 (Dragon-Tiger Board client).

基于 akshare 的东方财富龙虎榜接口拉取近 N 天的龙虎榜数据，
提取机构净买入信号供 Explorer 规则引擎使用。

设计原则:
- 单一职责: 只负责拉取和聚合数据，不做业务判断
- 本地缓存: 同一天内多次调用只拉一次
- 失败降级: akshare 出错时返回空字典，不阻塞主流程
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 禁用代理（与 akshare_client.py 一致）
for var in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY']:
    if var in os.environ:
        del os.environ[var]
os.environ['NO_PROXY'] = '*'


@dataclass
class DragonTigerRecord:
    """单只股票在指定时间窗口内的龙虎榜聚合数据。"""
    symbol: str
    name: str = ""
    appearance_count: int = 0              # 上榜次数
    total_net_buy_amount: float = 0.0      # 总净买入金额（元）
    institutional_net_buy: float = 0.0     # 机构专用席位净买入（元）
    institutions_buy_count: int = 0        # 有机构买入的次数
    latest_reason: str = ""                # 最近一次上榜原因
    dates: list[str] = field(default_factory=list)  # 上榜日期列表

    @property
    def net_buy_wan(self) -> float:
        return self.total_net_buy_amount / 1e4

    @property
    def institutional_net_buy_wan(self) -> float:
        return self.institutional_net_buy / 1e4

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "appearance_count": self.appearance_count,
            "total_net_buy_wan": round(self.net_buy_wan, 1),
            "institutional_net_buy_wan": round(self.institutional_net_buy_wan, 1),
            "institutions_buy_count": self.institutions_buy_count,
            "latest_reason": self.latest_reason,
            "dates": self.dates,
        }


class DragonTigerClient:
    """龙虎榜数据客户端 — 基于 akshare 的东财接口。"""

    def __init__(self, cache_ttl_hours: int = 6) -> None:
        self._cache: dict[str, tuple[datetime, dict[str, DragonTigerRecord]]] = {}
        self._cache_ttl = timedelta(hours=cache_ttl_hours)

    def fetch_recent(self, days_back: int = 3) -> dict[str, DragonTigerRecord]:
        """拉取最近 N 天的龙虎榜数据，按 symbol 聚合。

        使用两个 akshare 接口:
        - stock_lhb_detail_em: 龙虎榜整体明细（总净买入、上榜次数、原因）
        - stock_lhb_jgmmtj_em: 机构买卖每日统计（机构买入净额、机构数量）

        Args:
            days_back: 回溯天数 (默认 3 天，覆盖机构近期动向)

        Returns:
            {symbol: DragonTigerRecord}
        """
        cache_key = f"recent_{days_back}"
        if cache_key in self._cache:
            cached_at, data = self._cache[cache_key]
            if datetime.now() - cached_at < self._cache_ttl:
                logger.debug("使用龙虎榜缓存: %d 只股票", len(data))
                return data

        try:
            import akshare as ak
        except ImportError:
            logger.warning("akshare 未安装，龙虎榜数据源不可用")
            return {}

        end_date = date.today()
        start_date = end_date - timedelta(days=days_back)
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")

        records: dict[str, DragonTigerRecord] = {}

        # 1. 拉取龙虎榜整体明细
        try:
            df = ak.stock_lhb_detail_em(start_date=start_str, end_date=end_str)
        except Exception as e:
            logger.warning("龙虎榜明细拉取失败: %s", e)
            df = None

        if df is not None and not df.empty:
            for _, row in df.iterrows():
                symbol = str(row.get("代码", "")).zfill(6)
                if not symbol or not symbol.isdigit():
                    continue

                name = str(row.get("名称", ""))
                reason = str(row.get("上榜原因", ""))
                trade_date = str(row.get("上榜日", ""))
                net_buy = _safe_float(row.get("龙虎榜净买额", 0))

                rec = records.get(symbol)
                if rec is None:
                    rec = DragonTigerRecord(symbol=symbol, name=name)
                    records[symbol] = rec

                rec.appearance_count += 1
                rec.total_net_buy_amount += net_buy
                if trade_date and trade_date not in rec.dates:
                    rec.dates.append(trade_date)
                if not rec.latest_reason or (trade_date and rec.dates and trade_date == max(rec.dates)):
                    rec.latest_reason = reason

        # 2. 拉取机构买卖每日统计（获取机构专用席位数据）
        try:
            jg_df = ak.stock_lhb_jgmmtj_em(start_date=start_str, end_date=end_str)
        except Exception as e:
            logger.warning("机构买卖统计拉取失败 (不影响主流程): %s", e)
            jg_df = None

        if jg_df is not None and not jg_df.empty:
            for _, row in jg_df.iterrows():
                symbol = str(row.get("代码", "")).zfill(6)
                if not symbol or not symbol.isdigit():
                    continue

                inst_net = _safe_float(row.get("机构买入净额", 0))
                buy_count = int(_safe_float(row.get("买方机构数", 0)))

                rec = records.get(symbol)
                if rec is None:
                    # 该股在整体明细中未出现，但机构表里有 — 仍记录
                    name = str(row.get("名称", ""))
                    rec = DragonTigerRecord(symbol=symbol, name=name)
                    rec.appearance_count = 1  # 至少认为上榜一次
                    records[symbol] = rec

                # 累加机构数据（多天可能有多条）
                rec.institutional_net_buy += inst_net
                if buy_count > 0 and inst_net > 0:
                    rec.institutions_buy_count += buy_count

        self._cache[cache_key] = (datetime.now(), records)
        inst_active = sum(1 for r in records.values() if r.institutional_net_buy > 0)
        logger.info(
            "龙虎榜数据加载: %d 只股票 (窗口 %s ~ %s), 其中 %d 只有机构净买入",
            len(records), start_date, end_date, inst_active,
        )
        return records

    def get_symbol(self, symbol: str, days_back: int = 3) -> Optional[DragonTigerRecord]:
        """便捷方法: 获取单只股票的龙虎榜记录。"""
        return self.fetch_recent(days_back=days_back).get(symbol.zfill(6))


def _safe_float(v) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0
