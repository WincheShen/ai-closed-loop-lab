"""扩充 strategy_lab 日线缓存，为 bottom_reversal / value_investing 凑够 n>=200 的真实样本。

只下载真实日线（腾讯/新浪回退，禁 mock），带成交额。多行业分散，降低单板块偏差。
用法: python scripts/expand_backtest_cache.py
"""
from __future__ import annotations

import sys
import time

from src.strategy_lab.data import load_cached, save_cached
from src.stock_analyzer.data_source.akshare_client import AkshareClient

# 价值槽：大盘蓝筹，多行业，PE 相对合理
VALUE_UNIVERSE = [
    "600519", "000858", "600809", "000568", "600887",  # 白酒/食品饮料
    "600036", "601166", "601318", "601288", "600016",  # 银行/保险
    "600900", "601088", "600028", "601857", "600585",  # 公用/能源/材料
    "000333", "000651", "600690", "002415", "000100",  # 家电/制造
    "600276", "300760", "600196", "002007", "000538",  # 医药
    "601012", "600438", "002459",                        # 光伏
    "600030", "601688", "300059",                        # 券商
    "601899", "603288", "600309", "000002", "600048",  # 材料/地产/消费
]

# 短线槽（bottom_reversal 纯价量）：中小盘、高波动、多板块
SHORT_UNIVERSE = [
    "300750", "002475", "002371", "603501", "688981",  # 半导体/电池
    "300124", "002230", "300274", "300782", "300418",  # 工控/AI/光伏
    "301171", "300684", "688143", "000636", "002297",  # 高弹性
    "300209", "002460", "300316", "603986", "688012",  # 锂/半导体设备
    "002241", "300347", "300496", "688111", "688036",  # 消费电子/CXO/软件
    "002714", "300142", "600438", "300759", "002709",  # 养殖/生物/新能源
    "300142", "603259", "300888", "002466", "300896",  # CXO/消费
    "688169", "301269", "300857", "605499", "002050",  # 机器人/新股/消费
]


def main() -> int:
    universe = sorted(set(VALUE_UNIVERSE) | set(SHORT_UNIVERSE))
    client = AkshareClient(allow_mock_fallback=False)
    ok, skip, fail = 0, 0, 0
    for i, sym in enumerate(universe, 1):
        cached = load_cached(sym)
        if len(cached) >= 480:
            skip += 1
            print(f"[{i}/{len(universe)}] {sym} 已缓存 {len(cached)} 根，跳过")
            continue
        try:
            bars = client.fetch_kline(sym, days=520)
            if bars and len(bars) >= 200:
                save_cached(sym, bars)
                ok += 1
                print(f"[{i}/{len(universe)}] {sym} OK {len(bars)} 根 ({bars[0].date}->{bars[-1].date})")
            else:
                fail += 1
                print(f"[{i}/{len(universe)}] {sym} 数据不足: {len(bars) if bars else 0} 根")
        except Exception as exc:  # noqa: BLE001
            fail += 1
            print(f"[{i}/{len(universe)}] {sym} 失败: {str(exc)[:60]}")
        time.sleep(0.4)
    print(f"\n完成: 新增 {ok}, 跳过 {skip}, 失败 {fail}, 总池 {len(universe)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
