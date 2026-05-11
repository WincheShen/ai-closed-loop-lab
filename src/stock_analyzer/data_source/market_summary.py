"""盘中走势摘要 — 将分钟K线压缩为 LLM 可读的结构化文本。

Position Review Agent 的输入不是原始 DataFrame，
而是经过压缩的文字摘要，控制 token 用量的同时保留关键信息。
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from src.stock_analyzer.data_source.intraday_client import IntradaySnapshot, MinuteBar


def summarize_intraday(
    snapshot: IntradaySnapshot,
    entry_price: Optional[float] = None,
    position_side: str = "long",
) -> str:
    """将盘中快照压缩为 LLM 可消费的文字摘要。

    Args:
        snapshot: 盘中快照（含分钟K线）
        entry_price: 持仓成本价（用于计算浮盈浮亏）
        position_side: "long" 或 "short"

    Returns:
        ~300-500 字的结构化摘要文本
    """
    bars = snapshot.bars
    if not bars:
        return f"[{snapshot.symbol}] 无盘中数据"

    lines: list[str] = []

    # --- Header ---
    lines.append(f"## {snapshot.symbol} {snapshot.name} 盘中走势")
    lines.append(f"当前价: {snapshot.current_price:.2f} | "
                 f"涨跌: {snapshot.change_pct:+.2f}% | "
                 f"振幅: {_amplitude(snapshot):.2f}%")
    lines.append(f"开盘: {snapshot.open:.2f} | "
                 f"最高: {snapshot.high:.2f} | "
                 f"最低: {snapshot.low:.2f}")
    lines.append(f"成交量: {_fmt_volume(snapshot.volume)} | "
                 f"成交额: {_fmt_turnover(snapshot.turnover)}")

    # --- P&L if holding ---
    if entry_price and entry_price > 0:
        pnl_pct = (snapshot.current_price / entry_price - 1) * 100
        if position_side == "short":
            pnl_pct = -pnl_pct
        lines.append(f"持仓成本: {entry_price:.2f} | 浮动盈亏: {pnl_pct:+.2f}%")

    lines.append("")

    # --- Price trajectory (key turning points) ---
    trajectory = _extract_trajectory(bars)
    if trajectory:
        lines.append("### 价格轨迹")
        lines.append(trajectory)
        lines.append("")

    # --- Volume analysis ---
    vol_summary = _volume_analysis(bars)
    if vol_summary:
        lines.append("### 量能特征")
        lines.append(vol_summary)
        lines.append("")

    # --- Simple technical signals ---
    tech = _simple_technicals(bars, snapshot)
    if tech:
        lines.append("### 技术信号")
        lines.append(tech)

    return "\n".join(lines)


def summarize_positions_batch(
    snapshots: list[tuple[IntradaySnapshot, float, str]],
) -> str:
    """批量摘要多只持仓股的盘中走势。

    Args:
        snapshots: [(snapshot, entry_price, side), ...]

    Returns:
        所有持仓的合并摘要
    """
    parts: list[str] = []
    for snap, entry, side in snapshots:
        parts.append(summarize_intraday(snap, entry, side))
        parts.append("---")
    return "\n".join(parts)


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _amplitude(snap: IntradaySnapshot) -> float:
    if snap.prev_close and snap.prev_close > 0:
        return (snap.high - snap.low) / snap.prev_close * 100
    return 0.0


def _fmt_volume(vol: float) -> str:
    if vol >= 1e8:
        return f"{vol / 1e8:.1f}亿手"
    if vol >= 1e4:
        return f"{vol / 1e4:.1f}万手"
    return f"{vol:.0f}手"


def _fmt_turnover(t: float) -> str:
    if t >= 1e8:
        return f"{t / 1e8:.2f}亿"
    if t >= 1e4:
        return f"{t / 1e4:.0f}万"
    return f"{t:.0f}元"


def _extract_trajectory(bars: list[MinuteBar]) -> str:
    """提取价格轨迹的关键转折点，用文字描述。"""
    if len(bars) < 3:
        return ""

    segments: list[str] = []
    # Find local extremes
    prices = [b.close for b in bars]
    n = len(prices)
    pivots: list[tuple[int, str, float]] = [(0, "开", prices[0])]

    for i in range(1, n - 1):
        if prices[i] > prices[i - 1] and prices[i] > prices[i + 1]:
            pivots.append((i, "高", prices[i]))
        elif prices[i] < prices[i - 1] and prices[i] < prices[i + 1]:
            pivots.append((i, "低", prices[i]))

    pivots.append((n - 1, "现", prices[-1]))

    # Keep at most 6 pivots for brevity
    if len(pivots) > 6:
        # Keep first, last, and the 4 most significant swings
        middle = pivots[1:-1]
        middle.sort(key=lambda p: abs(p[2] - prices[0]), reverse=True)
        pivots = [pivots[0]] + sorted(middle[:4], key=lambda p: p[0]) + [pivots[-1]]

    for idx, label, price in pivots:
        ts = bars[idx].timestamp.strftime("%H:%M")
        change = (price / prices[0] - 1) * 100
        segments.append(f"{ts}[{label}]{price:.2f}({change:+.1f}%)")

    return " → ".join(segments)


def _volume_analysis(bars: list[MinuteBar]) -> str:
    """量能分析：前半段 vs 后半段，放量/缩量判断。"""
    if len(bars) < 4:
        return ""

    mid = len(bars) // 2
    first_half_vol = sum(b.volume for b in bars[:mid])
    second_half_vol = sum(b.volume for b in bars[mid:])
    avg_vol = sum(b.volume for b in bars) / len(bars)

    # Find peak volume bar
    peak_bar = max(bars, key=lambda b: b.volume)
    peak_ts = peak_bar.timestamp.strftime("%H:%M")
    peak_ratio = peak_bar.volume / avg_vol if avg_vol > 0 else 1

    parts: list[str] = []

    if second_half_vol > first_half_vol * 1.5:
        parts.append("后半段放量明显（量比前半段 ×{:.1f}）".format(second_half_vol / first_half_vol if first_half_vol else 1))
    elif first_half_vol > second_half_vol * 1.5:
        parts.append("前半段放量后缩量")
    else:
        parts.append("量能分布均匀")

    if peak_ratio > 3:
        parts.append(f"异常放量点: {peak_ts}（{peak_ratio:.1f}倍均量）")

    return "；".join(parts)


def _simple_technicals(bars: list[MinuteBar], snap: IntradaySnapshot) -> str:
    """简单盘中技术信号（不依赖外部库）。"""
    if len(bars) < 10:
        return ""

    signals: list[str] = []
    prices = [b.close for b in bars]
    n = len(prices)

    # 1. Trend: compare first third vs last third
    third = max(n // 3, 1)
    avg_first = sum(prices[:third]) / third
    avg_last = sum(prices[-third:]) / third
    if avg_last > avg_first * 1.01:
        signals.append("盘中趋势: 震荡上行")
    elif avg_last < avg_first * 0.99:
        signals.append("盘中趋势: 震荡下行")
    else:
        signals.append("盘中趋势: 横盘整理")

    # 2. Near high/low
    price = snap.current_price
    if snap.high > snap.low:
        pos_in_range = (price - snap.low) / (snap.high - snap.low)
        if pos_in_range > 0.9:
            signals.append("接近日内高点")
        elif pos_in_range < 0.1:
            signals.append("接近日内低点")

    # 3. MA crossover (5-bar vs 10-bar)
    if n >= 10:
        ma5 = sum(prices[-5:]) / 5
        ma10 = sum(prices[-10:]) / 10
        prev_ma5 = sum(prices[-6:-1]) / 5
        prev_ma10 = sum(prices[-11:-1]) / 10
        if prev_ma5 <= prev_ma10 and ma5 > ma10:
            signals.append("短期均线金叉")
        elif prev_ma5 >= prev_ma10 and ma5 < ma10:
            signals.append("短期均线死叉")

    # 4. Volume trend in last 5 bars
    if n >= 10:
        recent_vol = sum(b.volume for b in bars[-5:]) / 5
        earlier_vol = sum(b.volume for b in bars[-10:-5]) / 5
        if earlier_vol > 0:
            vol_change = recent_vol / earlier_vol
            if vol_change > 1.5:
                signals.append(f"近期放量（{vol_change:.1f}倍）")
            elif vol_change < 0.5:
                signals.append("近期缩量")

    return "；".join(signals)
