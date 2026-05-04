"""AKShare 行情拉取（带 mock fallback）。

Phase 1 目标：
- 真实接口可用时拉真数据
- 不可用时（无网/被限流）自动降级到 mock，保证流水线可跑通
- 所有外部数据统一封装为 StockQuote / MarketSnapshot 数据类
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class StockQuote:
    symbol: str                    # 6 位代码，不带交易所前缀
    name: str
    price: float
    change_pct: float              # 当日涨跌幅 %
    volume: float                  # 成交量（手）
    turnover: float                # 成交额（元）
    turnover_rate: float = 0.0     # 换手率 %
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    market_cap_yi: Optional[float] = None  # 总市值（亿元）
    industry: str = ""
    main_fund_net_inflow: float = 0.0  # 主力净流入（元）

    @property
    def is_st(self) -> bool:
        return "ST" in self.name.upper()


@dataclass
class SectorQuote:
    name: str
    change_pct: float
    turnover: float
    leading_stocks: list[str] = field(default_factory=list)
    main_fund_net_inflow: float = 0.0


@dataclass
class MarketSnapshot:
    snapshot_date: date
    stocks: list[StockQuote]
    sectors: list[SectorQuote]
    is_mock: bool = False


class AkshareClient:
    """统一的行情入口。"""

    def __init__(self, allow_mock_fallback: bool = True):
        self.allow_mock_fallback = allow_mock_fallback
        try:
            import akshare as ak  # noqa: F401
            self._ak_available = True
        except ImportError:
            logger.warning("akshare 未安装，将使用 mock 数据")
            self._ak_available = False

    def fetch_snapshot(self) -> MarketSnapshot:
        if self._ak_available:
            try:
                return self._fetch_real()
            except Exception as e:  # noqa: BLE001
                logger.warning("akshare 真实拉取失败：%s，降级到 mock", e)
                if not self.allow_mock_fallback:
                    raise
        return self._fetch_mock()

    # ------------------------------------------------------------------
    # Real
    # ------------------------------------------------------------------

    def _fetch_real(self) -> MarketSnapshot:
        """真实数据拉取。

        TODO(Phase 2): 完整接入
            - ak.stock_zh_a_spot_em()        全市场实时行情
            - ak.stock_board_industry_name_em()  行业板块
            - ak.stock_individual_fund_flow_rank()  资金流
        """
        import akshare as ak  # type: ignore

        df = ak.stock_zh_a_spot_em()  # 全市场快照
        # 字段映射（参考 akshare 文档）：
        # 代码 / 名称 / 最新价 / 涨跌幅 / 成交量 / 成交额 / 换手率 / 市盈率-动态 / 市净率 / 总市值
        stocks: list[StockQuote] = []
        for _, row in df.iterrows():
            try:
                stocks.append(StockQuote(
                    symbol=str(row.get("代码", "")).zfill(6),
                    name=str(row.get("名称", "")),
                    price=float(row.get("最新价", 0) or 0),
                    change_pct=float(row.get("涨跌幅", 0) or 0),
                    volume=float(row.get("成交量", 0) or 0),
                    turnover=float(row.get("成交额", 0) or 0),
                    turnover_rate=float(row.get("换手率", 0) or 0),
                    pe_ttm=_safe_float(row.get("市盈率-动态")),
                    pb=_safe_float(row.get("市净率")),
                    market_cap_yi=_safe_float(row.get("总市值"), divisor=1e8),
                ))
            except Exception:  # noqa: BLE001
                continue

        # 板块行情（简化版）
        sectors: list[SectorQuote] = []
        try:
            sec_df = ak.stock_board_industry_name_em()
            for _, row in sec_df.iterrows():
                sectors.append(SectorQuote(
                    name=str(row.get("板块名称", "")),
                    change_pct=float(row.get("涨跌幅", 0) or 0),
                    turnover=float(row.get("总成交额", 0) or 0),
                ))
        except Exception as e:  # noqa: BLE001
            logger.warning("板块数据拉取失败：%s", e)

        logger.info("AKShare 实时快照：%d 只股票，%d 个板块", len(stocks), len(sectors))
        return MarketSnapshot(
            snapshot_date=date.today(),
            stocks=stocks,
            sectors=sectors,
            is_mock=False,
        )

    # ------------------------------------------------------------------
    # Mock
    # ------------------------------------------------------------------

    def _fetch_mock(self) -> MarketSnapshot:
        rng = random.Random(date.today().toordinal())
        sector_pool = ["低空经济", "AI算力", "AI手机", "光伏储能", "创新药",
                       "军工", "白酒", "新能源车", "半导体", "传媒"]
        stocks: list[StockQuote] = []
        for i in range(50):
            sym = f"60{i:04d}"
            stocks.append(StockQuote(
                symbol=sym,
                name=f"模拟股{i:03d}",
                price=round(rng.uniform(5, 100), 2),
                change_pct=round(rng.uniform(-5, 9), 2),
                volume=rng.uniform(1e5, 1e7),
                turnover=rng.uniform(1e7, 5e9),
                turnover_rate=round(rng.uniform(0.5, 18), 2),
                pe_ttm=round(rng.uniform(5, 80), 1),
                pb=round(rng.uniform(0.5, 10), 1),
                market_cap_yi=round(rng.uniform(20, 3000), 1),
                industry=rng.choice(sector_pool),
                main_fund_net_inflow=rng.uniform(-2e8, 5e8),
            ))
        sectors = [
            SectorQuote(
                name=name,
                change_pct=round(rng.uniform(-3, 7), 2),
                turnover=rng.uniform(1e9, 5e10),
                main_fund_net_inflow=rng.uniform(-1e9, 5e9),
            )
            for name in sector_pool
        ]
        return MarketSnapshot(
            snapshot_date=date.today(),
            stocks=stocks,
            sectors=sectors,
            is_mock=True,
        )


def _safe_float(value: object, divisor: float = 1.0) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value) / divisor
    except (TypeError, ValueError):
        return None
