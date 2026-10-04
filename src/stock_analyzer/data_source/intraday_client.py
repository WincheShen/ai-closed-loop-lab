"""盘中行情客户端 — 分钟K线 + 当日分时数据（带 mock fallback）。

提供两类数据：
1. MinuteBar: 分钟级K线 (1/5/15/30/60分钟)，用于技术指标计算
2. IntradayTick: 当日分时成交明细，用于量价分析

数据源：东方财富（通过 AKShare）
降级策略：与 akshare_client.py 一致 — 接口失败时自动降级到 mock 数据
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal, Optional

logger = logging.getLogger(__name__)

PeriodType = Literal["1", "5", "15", "30", "60"]


@dataclass
class MinuteBar:
    """单根分钟K线。"""

    timestamp: datetime          # K线开始时间
    open: float
    high: float
    low: float
    close: float
    volume: float                # 手
    turnover: float              # 元
    change_pct: float = 0.0      # 涨跌幅 %


@dataclass
class IntradayTick:
    """当日分时成交点。"""

    timestamp: datetime
    price: float
    volume: float                # 手
    avg_price: float             # 均价
    change_pct: float = 0.0      # 较昨收涨跌幅 %


@dataclass
class IntradaySnapshot:
    """一只股票的盘中快照，汇总分钟K线 + 当前状态。"""

    symbol: str
    name: str
    current_price: float
    prev_close: float
    change_pct: float
    high: float
    low: float
    open: float
    volume: float                # 当日总成交量（手）
    turnover: float              # 当日总成交额（元）
    bars: list[MinuteBar] = field(default_factory=list)
    is_mock: bool = False


def _sina_symbol(symbol: str) -> str:
    """根据股票代码前缀转换为新浪行情代码。"""
    if symbol.startswith(("6", "9")):
        return f"sh{symbol}"
    if symbol.startswith(("4", "8", "920")):
        return f"bj{symbol}"
    return f"sz{symbol}"


class IntradayClient:
    """盘中行情入口 — 分钟K线 + 分时数据。"""

    def __init__(self, allow_mock_fallback: bool = True) -> None:
        self.allow_mock_fallback = allow_mock_fallback
        self._last_source = "none"
        try:
            import akshare  # noqa: F401
            self._ak_available = True
        except ImportError:
            logger.warning("akshare 未安装，盘中行情将使用 mock 数据")
            self._ak_available = False

    def fetch_minute_bars(
        self,
        symbol: str,
        period: PeriodType = "30",
        limit: int = 48,
    ) -> list[MinuteBar]:
        """拉取分钟K线。

        数据源优先级：akshare(eastmoney) → 新浪财经 → mock

        Args:
            symbol: 6位股票代码
            period: K线周期 '1'/'5'/'15'/'30'/'60'
            limit: 返回最近N根K线

        Returns:
            按时间升序的 MinuteBar 列表
        """
        self._last_source = "none"
        if self._ak_available:
            try:
                bars = self._fetch_minute_real(symbol, period, limit)
                if bars:
                    self._last_source = "eastmoney"
                    return bars
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "分钟K线拉取失败 %s(period=%s): %s，尝试新浪",
                    symbol, period, e,
                )
        try:
            bars = self._fetch_minute_sina(symbol, period, limit)
            if bars:
                self._last_source = "sina"
                return bars
        except Exception as e:  # noqa: BLE001
            logger.warning("新浪分钟K线也失败 %s: %s，降级 mock", symbol, e)
        if not self.allow_mock_fallback:
            raise RuntimeError(f"分钟K线不可用: {symbol}")
        bars = self._fetch_minute_mock(symbol, period, limit)
        self._last_source = "mock"
        return bars

    def fetch_intraday_ticks(self, symbol: str) -> list[IntradayTick]:
        """拉取当日分时成交数据。

        Args:
            symbol: 6位股票代码

        Returns:
            按时间升序的 IntradayTick 列表
        """
        if self._ak_available:
            try:
                return self._fetch_ticks_real(symbol)
            except Exception as e:  # noqa: BLE001
                logger.warning("分时数据拉取失败 %s: %s，降级 mock", symbol, e)
                if not self.allow_mock_fallback:
                    raise
        return self._fetch_ticks_mock(symbol)

    def fetch_intraday_snapshot(
        self,
        symbol: str,
        name: str = "",
        period: PeriodType = "30",
        bar_limit: int = 48,
    ) -> IntradaySnapshot:
        """获取一只股票的完整盘中快照（聚合分钟K线和统计信息）。

        高可用保障：
        1. 优先拉取分钟K线 (AKShare / 新浪API)；
        2. 结合新浪实时行情 (hq.sinajs.cn) 获取当日全量盘中统计（最高/最低/成交额/均价）；
        3. 若分钟K线偶发失败，由秒级实时行情兜底合成快照，防止盘中风控与脉冲策略因网络波动漏触发。
        """
        try:
            bars = self.fetch_minute_bars(symbol, period, bar_limit)
        except Exception as e:
            logger.warning("拉取分钟K线失败 %s: %s", symbol, e)
            bars = []

        quote = self._fetch_quote_sina(symbol)

        if not bars:
            if quote and quote["current_price"] > 0:
                # 分钟K线失败但实时行情正常时，用实时快照兜底
                now_bar = MinuteBar(
                    timestamp=datetime.now(),
                    open=quote["open"],
                    high=quote["high"],
                    low=quote["low"],
                    close=quote["current_price"],
                    volume=quote["volume"],
                    turnover=quote["turnover"],
                    change_pct=quote["change_pct"],
                )
                self._last_source = "sina_quote"
                return IntradaySnapshot(
                    symbol=symbol,
                    name=quote.get("name") or name,
                    current_price=quote["current_price"],
                    prev_close=quote["prev_close"],
                    change_pct=quote["change_pct"],
                    high=quote["high"],
                    low=quote["low"],
                    open=quote["open"],
                    volume=quote["volume"],
                    turnover=quote["turnover"],
                    bars=[now_bar],
                    is_mock=False,
                )

            if not self.allow_mock_fallback:
                raise RuntimeError(f"实时行情与分钟K线均不可用: {symbol}")

            return IntradaySnapshot(
                symbol=symbol, name=name,
                current_price=0, prev_close=0, change_pct=0,
                high=0, low=0, open=0, volume=0, turnover=0,
                bars=[], is_mock=True,
            )

        latest = bars[-1]
        first = bars[0]
        curr_p = latest.close
        prev_close = (
            quote["prev_close"]
            if (quote and quote["prev_close"] > 0)
            else (first.open / (1 + first.change_pct / 100) if first.change_pct else first.open)
        )
        change_pct = (
            quote["change_pct"]
            if quote
            else (round((latest.close / prev_close - 1) * 100, 2) if prev_close else 0)
        )
        high = quote["high"] if (quote and quote["high"] > 0) else max(b.high for b in bars)
        low = quote["low"] if (quote and quote["low"] > 0) else min(b.low for b in bars)
        open_p = quote["open"] if (quote and quote["open"] > 0) else first.open
        vol = quote["volume"] if (quote and quote["volume"] > 0) else sum(b.volume for b in bars)
        turnover = quote["turnover"] if (quote and quote["turnover"] > 0) else sum(b.turnover for b in bars)

        return IntradaySnapshot(
            symbol=symbol,
            name=quote.get("name") or name,
            current_price=curr_p,
            prev_close=round(prev_close, 2),
            change_pct=change_pct,
            high=high,
            low=low,
            open=open_p,
            volume=vol,
            turnover=turnover,
            bars=bars,
            is_mock=self._last_source == "mock",
        )

    # ------------------------------------------------------------------
    # Real implementations
    # ------------------------------------------------------------------

    def _fetch_minute_real(
        self, symbol: str, period: PeriodType, limit: int,
    ) -> list[MinuteBar]:
        import akshare as ak  # type: ignore

        df = ak.stock_zh_a_hist_min_em(symbol=symbol, period=period)
        bars: list[MinuteBar] = []
        for _, row in df.tail(limit).iterrows():
            try:
                ts_str = str(row.get("时间", ""))
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S") if ts_str else datetime.now()
                bars.append(MinuteBar(
                    timestamp=ts,
                    open=float(row.get("开盘", 0) or 0),
                    high=float(row.get("最高", 0) or 0),
                    low=float(row.get("最低", 0) or 0),
                    close=float(row.get("收盘", 0) or 0),
                    volume=float(row.get("成交量", 0) or 0),
                    turnover=float(row.get("成交额", 0) or 0),
                    change_pct=float(row.get("涨跌幅", 0) or 0),
                ))
            except Exception:  # noqa: BLE001
                continue
        return bars

    def _fetch_ticks_real(self, symbol: str) -> list[IntradayTick]:
        import akshare as ak  # type: ignore

        df = ak.stock_intraday_em(symbol=symbol)
        ticks: list[IntradayTick] = []
        today = date.today()
        for _, row in df.iterrows():
            try:
                time_str = str(row.get("时间", ""))
                ts = datetime.combine(today, datetime.strptime(time_str, "%H:%M:%S").time())
                ticks.append(IntradayTick(
                    timestamp=ts,
                    price=float(row.get("成交价", 0) or 0),
                    volume=float(row.get("手数", 0) or 0),
                    avg_price=float(row.get("均价", 0) or 0),
                    change_pct=float(str(row.get("涨跌幅", "0")).replace("%", "") or 0),
                ))
            except Exception:  # noqa: BLE001
                continue
        return ticks

    # ------------------------------------------------------------------
    # Sina fallback (push2.eastmoney.com 被封时使用)
    # ------------------------------------------------------------------

    _SINA_KLINE_URL_V2 = (
        "https://quotes.sina.cn/cn/api/json_v2.php"
        "/CN_MarketDataService.getKLineData"
    )
    _SINA_KLINE_URL_LEGACY = (
        "http://money.finance.sina.com.cn/quotes_service"
        "/api/json_v2.php/CN_MarketData.getKLineData"
    )
    _SINA_HEADERS = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    }

    def _fetch_minute_sina(
        self, symbol: str, period: PeriodType, limit: int,
    ) -> list[MinuteBar]:
        """通过新浪财经 API 拉取分钟K线（优先 quotes.sina.cn，备用 money.finance.sina）。"""
        import requests as _req

        sina_symbol = _sina_symbol(symbol)
        params = {
            "symbol": sina_symbol,
            "scale": int(period),
            "ma": "no",
            "datalen": limit,
        }

        data = None
        for url in (self._SINA_KLINE_URL_V2, self._SINA_KLINE_URL_LEGACY):
            try:
                resp = _req.get(
                    url, params=params,
                    headers=self._SINA_HEADERS, timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        break
            except Exception as e:
                logger.debug("新浪接口 %s 请求失败 %s: %s", url, symbol, e)

        if not data or not isinstance(data, list):
            return []

        bars: list[MinuteBar] = []
        prev_close = 0.0
        for item in data:
            try:
                ts_str = str(item.get("day", ""))
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                close = float(item.get("close", 0))
                change_pct = (
                    round((close / prev_close - 1) * 100, 2)
                    if prev_close > 0 else 0
                )
                amount = float(item.get("amount", 0) or 0)
                bars.append(MinuteBar(
                    timestamp=ts,
                    open=float(item.get("open", 0)),
                    high=float(item.get("high", 0)),
                    low=float(item.get("low", 0)),
                    close=close,
                    volume=float(item.get("volume", 0)) / 100,  # 股→手
                    turnover=amount,
                    change_pct=change_pct,
                ))
                prev_close = close
            except Exception:  # noqa: BLE001
                continue
        return bars[-limit:]

    def _fetch_quote_sina(self, symbol: str) -> dict[str, Any] | None:
        """从新浪实时行情源（hq.sinajs.cn）获取秒级快照。"""
        import requests as _req

        sina_symbol = _sina_symbol(symbol)
        url = f"http://hq.sinajs.cn/list={sina_symbol}"
        try:
            resp = _req.get(url, headers=self._SINA_HEADERS, timeout=5)
            if resp.status_code == 200 and '="' in resp.text:
                raw = resp.text.split('="')[1].split('";')[0]
                parts = raw.split(",")
                if len(parts) >= 10:
                    open_p = float(parts[1] or 0)
                    prev_close = float(parts[2] or 0)
                    curr_p = float(parts[3] or 0)
                    high_p = float(parts[4] or 0)
                    low_p = float(parts[5] or 0)
                    vol_shares = float(parts[8] or 0)
                    turnover = float(parts[9] or 0)
                    if curr_p > 0:
                        change_pct = (
                            round((curr_p / prev_close - 1) * 100, 2)
                            if prev_close > 0 else 0.0
                        )
                        return {
                            "name": parts[0],
                            "open": open_p,
                            "prev_close": prev_close,
                            "current_price": curr_p,
                            "high": high_p,
                            "low": low_p,
                            "volume": vol_shares / 100.0,
                            "turnover": turnover,
                            "change_pct": change_pct,
                        }
        except Exception as e:
            logger.debug("新浪实时行情获取失败 %s: %s", symbol, e)
        return None

    # ------------------------------------------------------------------
    # Mock implementations
    # ------------------------------------------------------------------

    def _fetch_minute_mock(
        self, symbol: str, period: PeriodType, limit: int,
    ) -> list[MinuteBar]:
        rng = random.Random(hash(symbol) + date.today().toordinal())
        period_mins = int(period)
        price = rng.uniform(8, 80)
        prev_close = price

        bars: list[MinuteBar] = []
        # Generate bars for today's trading session
        morning_start = time(9, 30)
        now = datetime.now()
        current_time = datetime.combine(date.today(), morning_start)

        for _ in range(limit):
            change = rng.gauss(0, 0.003)
            open_ = price
            close = round(price * (1 + change), 2)
            high = round(max(open_, close) * rng.uniform(1.0, 1.005), 2)
            low = round(min(open_, close) * rng.uniform(0.995, 1.0), 2)
            vol = rng.uniform(500, 50000)
            bars.append(MinuteBar(
                timestamp=current_time,
                open=round(open_, 2),
                high=high,
                low=low,
                close=close,
                volume=round(vol, 0),
                turnover=round(vol * close * 100, 0),
                change_pct=round((close / prev_close - 1) * 100, 2),
            ))
            price = close
            current_time += timedelta(minutes=period_mins)
            # Skip lunch break
            if current_time.time() >= time(11, 30) and current_time.time() < time(13, 0):
                current_time = current_time.replace(hour=13, minute=0)
            if current_time > now or current_time.time() >= time(15, 0):
                break
        return bars

    def _fetch_ticks_mock(self, symbol: str) -> list[IntradayTick]:
        rng = random.Random(hash(symbol) + date.today().toordinal())
        price = rng.uniform(8, 80)
        prev_close = price
        ticks: list[IntradayTick] = []
        current_time = datetime.combine(date.today(), time(9, 30))
        cumulative_vol = 0.0
        cumulative_turnover = 0.0

        for _ in range(120):
            change = rng.gauss(0, 0.001)
            price = round(price * (1 + change), 2)
            vol = rng.uniform(10, 5000)
            cumulative_vol += vol
            cumulative_turnover += vol * price * 100
            avg = round(cumulative_turnover / (cumulative_vol * 100), 2) if cumulative_vol else price
            ticks.append(IntradayTick(
                timestamp=current_time,
                price=price,
                volume=round(vol, 0),
                avg_price=avg,
                change_pct=round((price / prev_close - 1) * 100, 2),
            ))
            current_time += timedelta(minutes=2)
            if current_time.time() >= time(11, 30) and current_time.time() < time(13, 0):
                current_time = current_time.replace(hour=13, minute=0)
            if current_time.time() >= time(15, 0):
                break
        return ticks
