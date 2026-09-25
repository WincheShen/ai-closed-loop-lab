"""过去 10 交易日 (2026-09-07 ~ 2026-09-21) 超短线动量复刻策略回测仿真。

对比：
1. 原版静态日线追涨（不看天气、不设冷静期、收盘买收盘卖）
2. 复刻版高手完整体系（恶劣天气硬空仓 + 14:35 尾盘确认 + 09:35 早盘脉冲止盈/极窄止损 + T+1 强制清仓）
"""
from __future__ import annotations

import json
from pathlib import Path

# 标的映射
NAMES = {
    "300209": "行云科技",
    "300418": "昆仑万维",
    "301171": "易点天下",
    "002297": "博云新材",
    "300684": "中石科技",
    "688143": "长盈通",
    "000636": "风华高科",
    "002230": "科大讯飞",
    "002475": "立讯精密",
    "300750": "宁德时代",
}

# 真实大盘环境（来自 NAS 生产记录）
MARKET_REGIME = {
    "2026-09-07": {"regime": "neutral", "posture": "selective_attack", "up_ratio": 0.423},
    "2026-09-08": {"regime": "neutral", "posture": "selective_attack", "up_ratio": 0.604},
    "2026-09-09": {"regime": "bear",    "posture": "defend",           "up_ratio": 0.252},
    "2026-09-10": {"regime": "bear",    "posture": "defend",           "up_ratio": 0.106},
    "2026-09-11": {"regime": "bear",    "posture": "defend",           "up_ratio": 0.075},
    "2026-09-14": {"regime": "neutral", "posture": "selective_attack", "up_ratio": 0.580},
    "2026-09-15": {"regime": "bear",    "posture": "defend",           "up_ratio": 0.273},
    "2026-09-16": {"regime": "bear",    "posture": "defend",           "up_ratio": 0.275},
    "2026-09-17": {"regime": "bear",    "posture": "defend",           "up_ratio": 0.275},
    "2026-09-18": {"regime": "neutral", "posture": "selective_attack", "up_ratio": 0.797},
    "2026-09-21": {"regime": "neutral", "posture": "selective_attack", "up_ratio": 0.635},
}

DATES = [
    "2026-09-07",
    "2026-09-08",
    "2026-09-09",
    "2026-09-10",
    "2026-09-11",
    "2026-09-14",
    "2026-09-15",
    "2026-09-16",
    "2026-09-17",
    "2026-09-18",
    "2026-09-21",
]


def load_bars() -> dict[str, dict[str, dict]]:
    """加载所有标的的日 K 数据，按 {symbol: {date_str: bar_dict}} 组织。"""
    cache_dir = Path("data/strategy_lab/klines")
    data: dict[str, dict[str, dict]] = {}
    for symbol in NAMES:
        f = cache_dir / f"{symbol}.json"
        if not f.exists():
            continue
        content = json.loads(f.read_text())
        bars = content.get("bars", [])
        data[symbol] = {b["date"]: b for b in bars}
    return data


def run_master_replication(bars_db: dict[str, dict[str, dict]]):
    """运行复刻版高手的完整交易逻辑。"""
    print("=" * 80)
    print("【模式 B：复刻版高手完整体系回测 (2026-09-07 ~ 2026-09-21)】")
    print("规则：恶劣天气(上涨<30%)强行空仓 + 连亏冷静期 + 14:35尾盘买 + 09:35早盘冲高兑现/窄止损")
    print("=" * 80)

    capital = 300000.0
    cash = capital
    position: dict | None = None
    trades = []
    cooldown_until = None

    for i in range(len(DATES)):
        d_today = DATES[i]
        d_next = DATES[i + 1] if i + 1 < len(DATES) else None
        mkt = MARKET_REGIME.get(d_today, {})
        up_ratio = mkt.get("up_ratio", 0.5)

        print(f"\n📅 【{d_today}】 大盘状态: {mkt.get('regime')} | 上涨比例: {up_ratio:.1%} | 姿态: {mkt.get('posture')}")

        # ── 1. 早盘脉冲出场 (针对前一日已有持仓) ──
        if position:
            sym = position["symbol"]
            entry_p = position["entry_price"]
            name = NAMES.get(sym, sym)
            today_bar = bars_db.get(sym, {}).get(d_today)

            if today_bar:
                b_open = today_bar["open"]
                b_high = today_bar["high"]
                b_low = today_bar["low"]
                b_close = today_bar["close"]

                # 早盘脉冲逻辑
                exit_price = None
                exit_reason = ""
                hit_stop = False

                # 冲高止盈脉冲 (高点触及目标价 +8% 或早盘冲高 >= +3.5%)
                if b_high >= entry_p * 1.08:
                    exit_price = round(entry_p * 1.08, 2)
                    exit_reason = "🎯 触及 8% 目标价早盘止盈"
                elif b_high >= entry_p * 1.035 and b_open >= entry_p * 1.01:
                    # 早盘高开且脉冲冲高：在 +4% 附近兑现
                    exit_price = round(entry_p * 1.04, 2)
                    exit_reason = "⚡ 早盘冲高脉冲兑现 (+4.0%)"
                # 极窄硬止损 (触及 -2.5% 或破位)
                elif b_low <= entry_p * 0.975:
                    exit_price = round(entry_p * 0.975, 2)
                    exit_reason = "🛑 触发 2.5% 极窄硬止损"
                    hit_stop = True
                else:
                    # 14:30 强制 T+1 清仓
                    exit_price = b_close
                    pnl_tmp = (b_close / entry_p - 1) * 100
                    exit_reason = f"⏰ 14:30 隔夜强制清仓 ({pnl_tmp:+.2f}%)"
                    if pnl_tmp <= -2.5:
                        hit_stop = True

                pnl = (exit_price - entry_p) * position["qty"]
                pnl_pct = (exit_price / entry_p - 1) * 100
                cash += position["qty"] * exit_price - 5.0  # 扣手续费

                print(f"  [卖出] {sym} {name}: 成本={entry_p:.2f} -> 卖价={exit_price:.2f} | 盈亏: {pnl:+.2f} ({pnl_pct:+.2f}%) | 原因: {exit_reason}")
                trades.append({
                    "symbol": sym,
                    "name": name,
                    "buy_date": position["buy_date"],
                    "sell_date": d_today,
                    "entry_price": entry_p,
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "reason": exit_reason,
                })

                if hit_stop:
                    cooldown_until = d_next
                    print(f"  ⚠️ 触发止损，进入 1 天冷静期 (下次可开仓日期: {d_next} 之后)")

                position = None

        # ── 2. 尾盘 14:35 狙击 (选股建仓) ──
        if position is not None:
            print("  [仓位] 已有持仓，今日不再开新仓")
            continue

        if d_next is None:
            print(f"  🏁 回测期结束，今日尾盘不再开新仓")
            continue

        # 检查冷静期
        if cooldown_until and d_today <= cooldown_until:
            print(f"  🛑 [冷酷纪律] 处于大亏冷静期，今日全天 100% 现金空仓观望（不接飞刀）")
            continue

        # 检查恶劣天气硬闸门 (上涨比例 < 30%)
        if up_ratio < 0.30:
            print(f"  🌧️ [大盘硬闸] 全市场上涨比例 {up_ratio:.1%} < 30%，恶劣天气强制 0 仓位保命（复刻 9/15 清仓）")
            continue

        # 扫描自选股池候选标的
        candidates = []
        for sym, bars in bars_db.items():
            bar = bars.get(d_today)
            if not bar:
                continue
            to = bar.get("turnover", 0)
            rng = bar["high"] - bar["low"]
            amp = rng / bar["close"] if bar["close"] > 0 else 0
            chg = bar.get("change_pct", 0)
            close_pos = (bar["close"] - bar["low"]) / rng if rng > 0 else 0.5

            # 过滤：成交额 >= 3亿，振幅 >= 5%
            if to < 3e8 or amp < 0.05:
                continue

            # 形态 A：强势延续 (收红 + 收在振幅上 35% 高位区)
            if chg >= 1.0 and close_pos >= 0.65:
                candidates.append((sym, "强势延续", chg, to, close_pos, bar["close"]))
            # 形态 B：回调企稳 (收阳或下影线承接)
            elif -4.0 <= chg <= 1.0 and close_pos >= 0.50:
                candidates.append((sym, "强势企稳", chg, to, close_pos, bar["close"]))

        if not candidates:
            print(f"  🔍 自选池未发现满足 14:35 尾盘形态的标的，保持空仓")
            continue

        # 挑出得分最高的一只标的 (综合涨幅与收盘高位)
        candidates.sort(key=lambda x: (x[4] * 50 + x[2] * 2), reverse=True)
        best = candidates[0]
        sym_buy, setup, c_chg, c_to, c_cpos, buy_p = best
        name_buy = NAMES.get(sym_buy, sym_buy)

        buy_amount = min(cash * 0.30, 80000.0)  # 单笔上限 8 万
        qty = max(int(buy_amount / buy_p // 100) * 100, 100)
        actual_cost = qty * buy_p + 5.0
        cash -= actual_cost

        position = {
            "symbol": sym_buy,
            "name": name_buy,
            "buy_date": d_today,
            "entry_price": buy_p,
            "qty": qty,
        }
        print(f"  🎯 [14:35 买入] {sym_buy} {name_buy}: 现价={buy_p:.2f} | 股数={qty} | 投入={actual_cost:.2f} | 形态={setup} (涨幅={c_chg:+.2f}%, 收盘位={c_cpos:.1%})")

    # 统计总结
    print("\n" + "=" * 80)
    print("【回测统计报告】")
    print("=" * 80)
    print(f"交易笔数: {len(trades)} 笔")
    if trades:
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in trades)
        wr = len(wins) / len(trades) * 100
        win_sum = sum(t["pnl"] for t in wins)
        loss_sum = abs(sum(t["pnl"] for t in losses)) if losses else 1.0
        pf = win_sum / loss_sum if loss_sum > 0 else 999.0

        print(f"盈利笔数: {len(wins)} 笔 | 亏损笔数: {len(losses)} 笔")
        print(f"胜率: {wr:.1f}%")
        print(f"盈亏比 (Profit Factor): {pf:.2f}")
        print(f"总实现盈亏: {total_pnl:+.2f} 元 (基于 30 万本金回报率: {total_pnl/capital*100:+.2f}%)")
        print("\n逐笔交易明细:")
        for t in trades:
            print(f"  - {t['buy_date']} 买入 -> {t['sell_date']} 卖出 {t['symbol']} {t['name']}: 买={t['entry_price']:.2f}, 卖={t['exit_price']:.2f}, 盈亏={t['pnl']:+.2f} ({t['pnl_pct']:+.2f}%) [{t['reason']}]")
    else:
        print("未产生交易 (恶劣天气闸门全部拦截保护资金)")


def run_naive_comparison(bars_db: dict[str, dict[str, dict]]):
    """对比模式 A：普通无风控追涨模式（不看大盘天气、不设冷静期、尾盘买次日收盘卖）。"""
    print("\n" + "=" * 80)
    print("【模式 A：普通无风控日线追涨 (对比组)】")
    print("规则：每天按动量追涨，不看天气硬闸门，不设窄止损，死拿至次日收盘清仓")
    print("=" * 80)

    capital = 300000.0
    cash = capital
    trades = []
    position = None

    for i in range(len(DATES)):
        d_today = DATES[i]
        d_next = DATES[i + 1] if i + 1 < len(DATES) else None

        # 次日收盘清仓
        if position:
            sym = position["symbol"]
            entry_p = position["entry_price"]
            name = NAMES.get(sym, sym)
            today_bar = bars_db.get(sym, {}).get(d_today)
            if today_bar:
                close_p = today_bar["close"]
                pnl = (close_p - entry_p) * position["qty"]
                pnl_pct = (close_p / entry_p - 1) * 100
                cash += position["qty"] * close_p - 5.0
                trades.append({
                    "symbol": sym,
                    "name": name,
                    "buy_date": position["buy_date"],
                    "sell_date": d_today,
                    "entry_price": entry_p,
                    "exit_price": close_p,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                })
                print(f"  [收盘卖出] {sym} {name}: 成本={entry_p:.2f} -> 收盘价={close_p:.2f} | 盈亏: {pnl:+.2f} ({pnl_pct:+.2f}%)")
            position = None

        if d_next is None:
            continue

        # 每天只要有涨的就无脑买最强一只
        candidates = []
        for sym, bars in bars_db.items():
            b = bars.get(d_today)
            if b and b.get("turnover", 0) >= 3e8 and b.get("change_pct", 0) > 0:
                candidates.append((sym, b.get("change_pct", 0), b["close"]))

        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            top_sym, chg, p = candidates[0]
            buy_amt = min(cash * 0.30, 80000.0)
            qty = max(int(buy_amt / p // 100) * 100, 100)
            cash -= (qty * p + 5.0)
            position = {"symbol": top_sym, "name": NAMES.get(top_sym, top_sym), "buy_date": d_today, "entry_price": p, "qty": qty}
            print(f"  [盲目追涨] {d_today} 追入 {top_sym} {position['name']} @ {p:.2f} (当日涨幅 {chg:+.2f}%)")

    total_pnl = sum(t["pnl"] for t in trades)
    wr = len([t for t in trades if t["pnl"] > 0]) / len(trades) * 100 if trades else 0
    print("\n【模式 A 汇总】")
    print(f"交易笔数: {len(trades)} 笔 | 胜率: {wr:.1f}%")
    print(f"总实现盈亏: {total_pnl:+.2f} 元 (回报率: {total_pnl/capital*100:+.2f}%)")


if __name__ == "__main__":
    bars = load_bars()
    run_master_replication(bars)
    run_naive_comparison(bars)
