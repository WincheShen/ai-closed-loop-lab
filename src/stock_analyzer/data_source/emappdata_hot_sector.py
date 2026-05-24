"""基于 emappdata 热度榜推断热点板块。

由于 push2.eastmoney.com 板块接口被封，Sina API 不提供板块数据，
我们通过以下方式推断热点板块：
1. 从 emappdata.eastmoney.com 拉取 Top 100 热度股票
2. 从股票名称提取关键词（如"半导体"、"AI"、"新能源"）
3. 聚类成热点板块
4. 返回 SectorQuote 格式的板块数据
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from src.stock_analyzer.data_source.akshare_client import SectorQuote

logger = logging.getLogger(__name__)


# 板块关键词映射（人工维护，后续可考虑 LLM 自动提取）
_SECTOR_KEYWORDS = {
    "半导体": ["半导体", "芯片", "集成电路", "晶圆", "封测"],
    "人工智能": ["AI", "人工智能", "算力", "大模型", "算法", "智能"],
    "新能源车": ["新能源车", "电动车", "动力电池", "锂电", "充电桩", "新能源"],
    "光伏储能": ["光伏", "储能", "太阳能", "风电", "碳中和"],
    "消费电子": ["消费电子", "手机", "耳机", "面板", "显示"],
    "医疗器械": ["医疗", "医药", "生物", "疫苗", "诊断"],
    "军工": ["军工", "航天", "航空", "导弹", "雷达"],
    "白酒": ["白酒", "酒", "茅台", "五粮液"],
    "房地产": ["地产", "房地产", "物业"],
    "银行": ["银行", "金融"],
    "证券": ["证券", "券商", "期货"],
    "传媒": ["传媒", "游戏", "影视", "广告"],
    "电力": ["电力", "火电", "水电", "核电"],
    "煤炭": ["煤炭", "煤"],
    "有色金属": ["有色", "铜", "铝", "锂", "钴"],
    "钢铁": ["钢铁", "钢"],
    "化工": ["化工", "化学"],
    "机械": ["机械", "设备", "工程机械"],
    "汽车": ["汽车", "整车"],
    "家电": ["家电", "空调", "冰箱", "洗衣机"],
}


@dataclass
class HotStock:
    """emappdata 热度股票。"""
    symbol: str  # 股票代码，如 "SZ000725"
    rank: int  # 热度排名
    rank_change: int  # 排名变化
    historical_rank: int  # 历史排名


class EmappdataHotSectorDetector:
    """基于 emappdata 热度榜推断热点板块。"""

    _EMAPPDATA_URL = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    def __init__(self, top_k: int = 100):
        """初始化。

        Args:
            top_k: 拉取热度榜前 K 只股票
        """
        self.top_k = top_k

    def fetch_hot_stocks(self) -> list[HotStock]:
        """拉取 emappdata 热度榜。

        Returns:
            热度股票列表
        """
        try:
            resp = requests.post(
                self._EMAPPDATA_URL,
                json={"pageNo": 1, "pageSize": self.top_k},
                headers=self._HEADERS,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            items = data.get("data", [])
            if not items:
                logger.warning("emappdata 返回空数据")
                return []

            hot_stocks = [
                HotStock(
                    symbol=item["sc"],
                    rank=item["rk"],
                    rank_change=item["rc"],
                    historical_rank=item["hisRc"],
                )
                for item in items
            ]

            logger.info("emappdata 热度榜: %d 只股票", len(hot_stocks))
            return hot_stocks

        except Exception as e:
            logger.warning("emappdata 拉取失败: %s", e)
            return []

    def extract_keywords(self, name: str) -> list[str]:
        """从股票名称提取板块关键词。

        Args:
            name: 股票名称

        Returns:
            匹配的板块关键词列表
        """
        matched = []
        for sector, keywords in _SECTOR_KEYWORDS.items():
            for kw in keywords:
                if len(kw) >= 2 and kw in name:
                    matched.append(sector)
                    break  # 每个板块只匹配一次
        return matched

    def detect(self, stock_names: dict[str, str], stock_data: dict[str, tuple[float, float, float]] | None = None) -> list[SectorQuote]:
        """推断热点板块。

        Args:
            stock_names: 股票代码 -> 名称的映射（从快照中获取）
                         代码格式应为 6 位数字，如 "000725"
            stock_data: 可选，股票代码 -> (change_pct, turnover, main_fund_net_inflow)
                        用于计算板块级别指标

        Returns:
            热点板块列表（按热度排序）
        """
        # 1. 拉取热度榜
        hot_stocks = self.fetch_hot_stocks()
        if not hot_stocks:
            logger.warning("热度榜为空，无法推断板块")
            return []

        # 2. 聚类板块
        sector_stocks: dict[str, list[HotStock]] = {}
        sector_symbols: dict[str, list[str]] = {}  # 板块 -> 成分股代码列表
        for stock in hot_stocks:
            # 统一代码格式：去掉交易所前缀，补齐 6 位
            clean_symbol = stock.symbol.replace("SH", "").replace("SZ", "").zfill(6)
            name = stock_names.get(clean_symbol, "")
            if not name:
                logger.debug("股票 %s (%s) 未在快照中找到", clean_symbol, stock.symbol)
                continue

            keywords = self.extract_keywords(name)
            for sector in keywords:
                if sector not in sector_stocks:
                    sector_stocks[sector] = []
                    sector_symbols[sector] = []
                sector_stocks[sector].append(stock)
                sector_symbols[sector].append(clean_symbol)

        if not sector_stocks:
            logger.warning("聚类后无板块数据")
            return []

        # 3. 计算板块热度（股票数量 + 排名权重）
        sector_scores = []
        for sector, stocks in sector_stocks.items():
            # 热度分数：股票数量 * 10 + (100 - 平均排名)
            avg_rank = sum(s.rank for s in stocks) / len(stocks)
            score = len(stocks) * 10 + (100 - avg_rank)
            sector_scores.append((sector, score, stocks))

        # 4. 排序并转换为 SectorQuote（尽量填充真实指标）
        sector_scores.sort(key=lambda x: x[1], reverse=True)

        result = []
        for sector, score, stocks in sector_scores[:5]:
            symbols = sector_symbols[sector]
            leading = [s.symbol.replace("SH", "").replace("SZ", "").zfill(6) for s in stocks[:3]]

            # 从快照股票数据计算板块级指标
            change_pct = 0.0
            turnover = 0.0
            main_fund = 0.0
            if stock_data:
                pcts, tos, mfs = [], 0.0, 0.0
                for sym in symbols:
                    if sym in stock_data:
                        pct, to, mf = stock_data[sym]
                        pcts.append(pct)
                        tos += to
                        mfs += mf
                if pcts:
                    change_pct = sum(pcts) / len(pcts)
                    turnover = tos
                    main_fund = mfs

            result.append(SectorQuote(
                name=sector,
                change_pct=round(change_pct, 2),
                turnover=turnover,
                leading_stocks=leading,
                main_fund_net_inflow=main_fund,
            ))

        logger.info(
            "推断热点板块: %s",
            [f"{s.name}({s.change_pct:+.1f}%)" for s in result],
        )
        return result
