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
from datetime import date, timedelta
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


@dataclass
class KlineBar:
    """单根日K线。"""
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float       # 手
    turnover: float     # 元
    turnover_rate: float = 0.0  # 换手率 %
    change_pct: float = 0.0


_INDUSTRY_CACHE: dict[str, list[str]] = {}   # date_str -> list of board names
_MOCK_INDUSTRIES = [
    "半导体", "消费电子", "电子元件", "光学光电子",
    "新能源汽车", "电池", "储能设备", "光伏设备",
    "人工智能", "云计算", "软件开发", "算力基础设施",
    "医疗器械", "生物制品", "创新药", "医疗服务",
    "军工", "航空航天", "船舶制造",
    "白酒", "食品饮料", "商业百货",
    "银行", "保险", "证券",
    "房地产", "建筑材料", "工程机械",
    "煤炭", "有色金属", "钢铁",
    "传媒", "游戏", "互联网电商",
]


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
                logger.warning("akshare 拉取失败：%s，尝试新浪 API", e)
        # Sina fallback (push2.eastmoney.com 被封时)
        try:
            return self._fetch_sina()
        except Exception as e:  # noqa: BLE001
            logger.warning("新浪 API 也失败：%s，降级到 mock", e)
        if not self.allow_mock_fallback:
            raise RuntimeError("所有数据源均不可用")
        return self._fetch_mock()

    def fetch_industry_list(self) -> list[str]:
        """获取东方财富行业板块名称列表（日级缓存）。

        Returns:
            板块名称列表，如 ["半导体", "新能源汽车", ...]
        """
        today = date.today().isoformat()
        if today in _INDUSTRY_CACHE:
            return _INDUSTRY_CACHE[today]

        if self._ak_available:
            try:
                import akshare as ak  # type: ignore
                df = ak.stock_board_industry_name_em()
                names = [str(row) for row in df["板块名称"].dropna().tolist()]
                _INDUSTRY_CACHE.clear()
                _INDUSTRY_CACHE[today] = names
                logger.info("Industry list loaded: %d boards", len(names))
                return names
            except Exception as e:
                logger.warning("fetch_industry_list failed: %s，用 mock 列表", e)

        return _MOCK_INDUSTRIES

    def fetch_us_stock(self, symbol: str) -> Optional[StockQuote]:
        """拉取单只美股实时行情（东方财富美股接口）。

        Args:
            symbol: 美股代码，如 "AAPL", "TSLA"（可带 .US 后缀）

        Returns:
            StockQuote 或 None（未找到或接口失败）
        """
        if not self._ak_available:
            return None
        try:
            import akshare as ak  # type: ignore
            # 东方财富美股全量接口
            df = ak.stock_us_spot_em()
            # 统一代码格式：去掉 .US 后缀，转大写
            clean_symbol = symbol.upper().replace(".US", "").strip()
            match = df[df["代码"].str.upper() == clean_symbol]
            if match.empty:
                return None
            row = match.iloc[0]
            return StockQuote(
                symbol=clean_symbol,
                name=str(row.get("名称", clean_symbol)),
                price=float(row.get("最新价", 0) or 0),
                change_pct=float(row.get("涨跌幅", 0) or 0),
                volume=float(row.get("成交量", 0) or 0),
                turnover=float(row.get("成交额", 0) or 0),
                turnover_rate=0.0,  # 美股接口无换手率
                pe_ttm=_safe_float(row.get("市盈率")),
                pb=_safe_float(row.get("市净率")),
                market_cap_yi=_safe_float(row.get("总市值"), divisor=1e8),
                industry="美股",  # 东方财富美股接口无细分行业
            )
        except Exception as e:
            logger.warning("fetch_us_stock failed for %s: %s", symbol, e)
            return None

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
    # Sina fallback (push2.eastmoney.com 被封时使用)
    # ------------------------------------------------------------------

    _SINA_HQ_URL = (
        "http://vip.stock.finance.sina.com.cn/quotes_service"
        "/api/json_v2.php/Market_Center.getHQNodeData"
    )
    _SINA_KLINE_URL = (
        "http://money.finance.sina.com.cn/quotes_service"
        "/api/json_v2.php/CN_MarketData.getKLineData"
    )
    _SINA_HEADERS = {"Referer": "http://finance.sina.com.cn"}

    def _fetch_sina(self) -> MarketSnapshot:
        """通过新浪财经 API 拉取全 A 股行情快照。

        新浪 Market_Center 接口分页返回所有 A 股实时行情，
        字段覆盖：代码/名称/最新价/涨跌幅/成交量/成交额/换手率/PE/PB/市值。
        不含行业板块数据，HotSectorDetector 会返回空列表。

        容错策略 (避免单页超时导致整体降级到 mock):
        - 单页失败重试 2 次, 每次 sleep 1s
        - 已经拿到 >= 1000 只票时, 任何后续单页失败都跳过, 不抛异常
        - 完全失败的页数 > 5 才抛异常让外层降级
        """
        import time
        import requests as _req

        all_data: list[dict] = []
        failed_pages: list[int] = []
        # num 最大 100 (sina 限制); 加 250ms 请求间隔避免触发 rate limit (HTTP 456)
        for page in range(1, 70):  # ~5500 stocks / 100 per page ≈ 56 pages
            params = {
                "page": page, "num": 100,
                "sort": "symbol", "asc": 1,
                "node": "hs_a", "_s_r_a": "init",
            }
            batch: list[dict] | None = None
            for attempt in range(3):
                try:
                    resp = _req.get(
                        self._SINA_HQ_URL, params=params,
                        headers=self._SINA_HEADERS, timeout=15,
                    )
                    resp.raise_for_status()
                    batch = resp.json()
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt < 2:
                        # 456/429 类限流退避更久
                        backoff = 3.0 if "456" in str(e) or "429" in str(e) else 1.0
                        time.sleep(backoff * (attempt + 1))
                        continue
                    logger.warning(
                        "新浪 page=%d 拉取失败 (重试 3 次): %s",
                        page, str(e)[:80],
                    )
                    failed_pages.append(page)

            if batch is None:
                # 已经拿到一定数据就容忍这页失败
                if len(all_data) >= 1000 and len(failed_pages) <= 5:
                    continue
                # 失败页太多, 让外层降级
                if len(failed_pages) > 5:
                    raise RuntimeError(
                        f"新浪 API 失败页数过多: {failed_pages}",
                    )
                continue

            if not batch:
                break
            all_data.extend(batch)
            time.sleep(0.25)  # 限流缓解

        if failed_pages:
            logger.warning(
                "新浪 API 拉取期间失败页: %s, 但仍拿到 %d 条数据",
                failed_pages, len(all_data),
            )

        stocks: list[StockQuote] = []
        for item in all_data:
            try:
                price = float(item.get("trade", 0) or 0)
                if price <= 0:
                    continue
                stocks.append(StockQuote(
                    symbol=str(item.get("code", "")).zfill(6),
                    name=str(item.get("name", "")),
                    price=price,
                    change_pct=float(item.get("changepercent", 0) or 0),
                    volume=float(item.get("volume", 0) or 0) / 100,  # 股→手
                    turnover=float(item.get("amount", 0) or 0),
                    turnover_rate=float(item.get("turnoverratio", 0) or 0),
                    pe_ttm=_safe_float(item.get("per")),
                    pb=_safe_float(item.get("pb")),
                    market_cap_yi=_safe_float(item.get("mktcap"), divisor=1e4),
                ))
            except Exception:  # noqa: BLE001
                continue

        logger.info("新浪行情快照：%d 只股票（无板块数据）", len(stocks))
        return MarketSnapshot(
            snapshot_date=date.today(),
            stocks=stocks,
            sectors=[],  # 新浪不提供行业板块，HotSectorDetector 返回空
            is_mock=False,
        )

    def _fetch_kline_sina(self, symbol: str, days: int) -> list[KlineBar]:
        """通过新浪财经 API 拉取日K线。"""
        import requests as _req

        prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
        sina_symbol = f"{prefix}{symbol}"

        params = {
            "symbol": sina_symbol,
            "scale": 240,  # 日K (240 分钟 = 一个交易日)
            "ma": "no",
            "datalen": days,
        }
        resp = _req.get(
            self._SINA_KLINE_URL, params=params,
            headers=self._SINA_HEADERS, timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        bars: list[KlineBar] = []
        prev_close = 0.0
        for item in data:
            try:
                close = float(item.get("close", 0))
                change_pct = (
                    round((close / prev_close - 1) * 100, 2)
                    if prev_close > 0 else 0
                )
                bars.append(KlineBar(
                    date=date.fromisoformat(str(item.get("day", ""))[:10]),
                    open=float(item.get("open", 0)),
                    high=float(item.get("high", 0)),
                    low=float(item.get("low", 0)),
                    close=close,
                    volume=float(item.get("volume", 0)) / 100,  # 股→手
                    turnover=0.0,  # 新浪 K 线不含成交额
                    change_pct=change_pct,
                ))
                prev_close = close
            except Exception:  # noqa: BLE001
                continue
        return bars

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


    def fetch_kline(self, symbol: str, days: int = 60) -> list[KlineBar]:
        """拉取单只股票的日K线（最近 N 个交易日）。

        数据源优先级：akshare(eastmoney) → 新浪财经 → mock

        Args:
            symbol: 6位股票代码，如 "600519"
            days: 向前取多少个交易日（约=自然日数 * 5/7）

        Returns:
            按日期升序排列的 KlineBar 列表；失败或 mock 时返回合成数据
        """
        if self._ak_available:
            try:
                return self._fetch_kline_real(symbol, days)
            except Exception as e:
                logger.warning("akshare kline failed for %s: %s，尝试新浪", symbol, e)
        try:
            return self._fetch_kline_sina(symbol, days)
        except Exception as e:
            logger.warning("新浪 kline 也失败 %s: %s，降级 mock", symbol, e)
        if not self.allow_mock_fallback:
            raise RuntimeError(f"K线数据不可用: {symbol}")
        return self._fetch_kline_mock(symbol, days)

    def _fetch_kline_real(self, symbol: str, days: int) -> list[KlineBar]:
        import akshare as ak  # type: ignore

        end = date.today()
        start = end - timedelta(days=int(days * 1.8))  # 多取一些以覆盖节假日
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_str,
            end_date=end_str,
            adjust="hfq",   # 后复权
        )
        bars: list[KlineBar] = []
        for _, row in df.tail(days).iterrows():
            try:
                bars.append(KlineBar(
                    date=date.fromisoformat(str(row.get("日期", "")).replace("/", "-")),
                    open=float(row.get("开盘", 0) or 0),
                    high=float(row.get("最高", 0) or 0),
                    low=float(row.get("最低", 0) or 0),
                    close=float(row.get("收盘", 0) or 0),
                    volume=float(row.get("成交量", 0) or 0),
                    turnover=float(row.get("成交额", 0) or 0),
                    turnover_rate=float(row.get("换手率", 0) or 0),
                    change_pct=float(row.get("涨跌幅", 0) or 0),
                ))
            except Exception:
                continue
        return bars

    def _fetch_kline_mock(self, symbol: str, days: int) -> list[KlineBar]:
        rng = random.Random(hash(symbol))
        price = rng.uniform(10, 100)
        bars = []
        today = date.today()
        for i in range(days, 0, -1):
            change = rng.uniform(-0.05, 0.06)
            open_ = price
            close = round(price * (1 + change), 2)
            high = round(max(open_, close) * rng.uniform(1.0, 1.02), 2)
            low = round(min(open_, close) * rng.uniform(0.98, 1.0), 2)
            bars.append(KlineBar(
                date=today - timedelta(days=i),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=rng.uniform(1e5, 5e6),
                turnover=rng.uniform(1e7, 5e8),
                turnover_rate=round(rng.uniform(1, 15), 2),
                change_pct=round(change * 100, 2),
            ))
            price = close
        return bars


def _safe_float(value: object, divisor: float = 1.0) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value) / divisor
    except (TypeError, ValueError):
        return None
