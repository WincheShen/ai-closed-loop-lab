#!/usr/bin/env python3
"""验证 AKShare 盘中分钟K线真实数据拉取。

使用前：确保 ClashX 系统代理/TUN 已关闭，否则东方财富 API 会拒绝连接。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stock_analyzer.data_source.intraday_client import IntradayClient
from src.stock_analyzer.data_source.market_summary import summarize_intraday


def main() -> None:
    client = IntradayClient(allow_mock_fallback=False)  # 不允许 mock，强制走真实接口

    print("=" * 60)
    print("AKShare 盘中数据验证")
    print("=" * 60)

    # Test 1: 30分钟K线
    print("\n--- Test 1: 30分钟K线 (平安银行 000001) ---")
    try:
        bars = client.fetch_minute_bars("000001", period="30", limit=8)
        print(f"✅ 成功获取 {len(bars)} 根K线")
        for b in bars[-3:]:
            print(f"   {b.timestamp} | O={b.open:.2f} H={b.high:.2f} "
                  f"L={b.low:.2f} C={b.close:.2f} V={b.volume:.0f}")
    except Exception as e:
        print(f"❌ 失败: {e}")
        print("   → 请检查 ClashX 是否已关闭系统代理/TUN 模式")
        return

    # Test 2: 5分钟K线
    print("\n--- Test 2: 5分钟K线 (贵州茅台 600519) ---")
    try:
        bars = client.fetch_minute_bars("600519", period="5", limit=12)
        print(f"✅ 成功获取 {len(bars)} 根K线")
        for b in bars[-3:]:
            print(f"   {b.timestamp} | O={b.open:.2f} H={b.high:.2f} "
                  f"L={b.low:.2f} C={b.close:.2f} V={b.volume:.0f}")
    except Exception as e:
        print(f"❌ 失败: {e}")

    # Test 3: 分时数据
    print("\n--- Test 3: 当日分时数据 (平安银行 000001) ---")
    try:
        ticks = client.fetch_intraday_ticks("000001")
        print(f"✅ 成功获取 {len(ticks)} 条分时数据")
        if ticks:
            for t in ticks[-3:]:
                print(f"   {t.timestamp} | 价格={t.price:.2f} 均价={t.avg_price:.2f} "
                      f"量={t.volume:.0f} 涨跌={t.change_pct:+.2f}%")
    except Exception as e:
        print(f"❌ 失败: {e}")

    # Test 4: 完整快照 + LLM摘要
    print("\n--- Test 4: 完整快照 + LLM摘要 (北方华创 002371) ---")
    try:
        snap = client.fetch_intraday_snapshot("002371", name="北方华创", period="30")
        print(f"✅ 快照: {snap.symbol} {snap.name}")
        print(f"   价格={snap.current_price:.2f} 涨跌={snap.change_pct:+.2f}%")
        print(f"   K线数={len(snap.bars)} is_mock={snap.is_mock}")

        summary = summarize_intraday(snap, entry_price=320.0)
        print(f"\n📄 LLM摘要 ({len(summary)} 字):")
        print(summary)
    except Exception as e:
        print(f"❌ 失败: {e}")

    print("\n" + "=" * 60)
    print("验证完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
