"""新闻数据源 (Market News Client).

拉取全市场实时新闻，与股票代码/名称做关键词匹配，为 Explorer 规则和
Strategist LLM 提供 catalyst 信号。

设计原则:
- 简单启发式匹配（股名/代码在新闻中出现 + 正面关键词）
- 缓存 1 小时（新闻更新快，但同一个 pipeline 内不重复拉）
- 失败降级：返回空 dict，规则自动 miss
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 禁用代理
for var in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY']:
    if var in os.environ:
        del os.environ[var]
os.environ['NO_PROXY'] = '*'

# 正面/负面关键词（用于粗略情绪判断）
_POSITIVE_KEYWORDS = [
    "涨停", "利好", "突破", "创新高", "业绩预增", "预增", "订单", "中标",
    "签约", "合作", "并购", "重组", "转型", "投产", "扩产", "获批",
    "认证", "签订", "增持", "回购", "股东增持", "首富", "翻倍",
    "机构调研", "北向增持", "融资加仓", "解禁减少",
]
_NEGATIVE_KEYWORDS = [
    "跌停", "亏损", "预亏", "商誉减值", "违规", "处罚", "调查", "警示",
    "退市", "ST", "*ST", "停牌", "减持", "股东减持", "解禁增加",
    "诉讼", "败诉", "商誉", "计提", "债务违约", "延期披露",
]

_STOCK_CODE_RE = re.compile(r"\b(\d{6})\b")


@dataclass
class NewsItem:
    """单条新闻。"""
    title: str
    content: str = ""
    publish_time: str = ""
    url: str = ""
    source: str = "em"

    @property
    def sentiment(self) -> str:
        """粗略情绪判断: 'positive' / 'negative' / 'neutral'。"""
        text = self.title + self.content
        pos = sum(1 for kw in _POSITIVE_KEYWORDS if kw in text)
        neg = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in text)
        if pos > neg:
            return "positive"
        if neg > pos:
            return "negative"
        return "neutral"


@dataclass
class StockNewsRecord:
    """单只股票近期新闻聚合。"""
    symbol: str
    name: str = ""
    news_items: list[NewsItem] = field(default_factory=list)

    @property
    def positive_count(self) -> int:
        return sum(1 for n in self.news_items if n.sentiment == "positive")

    @property
    def negative_count(self) -> int:
        return sum(1 for n in self.news_items if n.sentiment == "negative")

    @property
    def has_positive_catalyst(self) -> bool:
        """近期存在正面 catalyst 且不被负面盖过。"""
        return self.positive_count > 0 and self.positive_count > self.negative_count

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "news_count": len(self.news_items),
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "has_positive_catalyst": self.has_positive_catalyst,
            "recent_titles": [n.title for n in self.news_items[:5]],
        }


class NewsClient:
    """全市场新闻客户端 — 拉取 + 按股票匹配。"""

    def __init__(self, cache_ttl_hours: int = 1) -> None:
        self._all_news: Optional[list[NewsItem]] = None
        self._fetched_at: Optional[datetime] = None
        self._cache_ttl = timedelta(hours=cache_ttl_hours)

    def fetch_global(self) -> list[NewsItem]:
        """拉取全市场最近新闻（东财实时新闻 ~200 条）。"""
        if self._all_news is not None and self._fetched_at is not None:
            if datetime.now() - self._fetched_at < self._cache_ttl:
                return self._all_news

        try:
            import akshare as ak
        except ImportError:
            logger.warning("akshare 未安装，新闻数据源不可用")
            return []

        news_items: list[NewsItem] = []
        try:
            df = ak.stock_info_global_em()
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    news_items.append(NewsItem(
                        title=str(row.get("标题", "")),
                        content=str(row.get("摘要", "")),
                        publish_time=str(row.get("发布时间", "")),
                        url=str(row.get("链接", "")),
                        source="em",
                    ))
        except Exception as e:
            logger.warning("东财实时新闻拉取失败: %s", e)

        self._all_news = news_items
        self._fetched_at = datetime.now()
        logger.info("新闻数据加载: %d 条 (source=em)", len(news_items))
        return news_items

    def match_to_stocks(
        self, stocks: list[tuple[str, str]], min_matches: int = 1,
    ) -> dict[str, StockNewsRecord]:
        """把新闻匹配到具体股票。

        Args:
            stocks: [(symbol, name), ...] 待匹配股票列表
            min_matches: 至少匹配 N 条才计入（默认 1）

        Returns:
            {symbol: StockNewsRecord}
        """
        news_items = self.fetch_global()
        if not news_items:
            return {}

        records: dict[str, StockNewsRecord] = {}
        # 预扫每条新闻里的 6 位代码
        for n in news_items:
            text = n.title + " " + n.content
            # 匹配股票代码
            for m in _STOCK_CODE_RE.finditer(text):
                code = m.group(1)
                if code not in records:
                    records[code] = StockNewsRecord(symbol=code)
                records[code].news_items.append(n)

        # 通过股票名称匹配（更精确但需要至少 3 字长以避免误报）
        for symbol, name in stocks:
            symbol = symbol.zfill(6)
            if not name or len(name) < 3:
                continue
            # 跳过已通过代码匹配的
            existing = records.get(symbol)
            existing_titles = {n.title for n in existing.news_items} if existing else set()

            for n in news_items:
                text = n.title + " " + n.content
                if name in text and n.title not in existing_titles:
                    if existing is None:
                        existing = StockNewsRecord(symbol=symbol, name=name)
                        records[symbol] = existing
                    existing.news_items.append(n)
                    existing_titles.add(n.title)

            if existing and not existing.name:
                existing.name = name

        # 过滤匹配数不足的
        filtered = {s: r for s, r in records.items() if len(r.news_items) >= min_matches}

        active_positive = sum(1 for r in filtered.values() if r.has_positive_catalyst)
        logger.info(
            "新闻匹配完成: %d 只股票有相关新闻, 其中 %d 只有正面催化",
            len(filtered), active_positive,
        )
        return filtered
