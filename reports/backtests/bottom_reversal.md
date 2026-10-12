# bottom_reversal

- assumptions: immediate=next_open*1.002; pullback=5d_low<=entry; breakout=5d_high>=entry*1.002; stop=daily_low then close; hard_exit=evaluate_hard_exit; hold<=10d
- universe: cached (79 symbols)
- n=274 wr=46.0% pf=1.332 dd=-13.771
- oos_n=98 oos_wr=44.9% expired=0
- passed=False

- [OK] G-bt-2: n=274 need>=200
- [NO] G-bt-3: wr=46.0% need>=48%
- [OK] G-bt-4: pf=1.332 need>=1.2
- [OK] G-bt-5: dd=-13.771 need<=15.0
- [OK] G-bt-6: oos_n=98 oos_wr=44.9% floor=38.0%
- [OK] G-bt-7: detectors imported from stock_analyzer.detectors

## 定义变更记录（2026-10-11）

- v1：「前 4 日跌 ≥6%」与「当日 MA5 上穿 MA10」要求同日成立，数学互斥
  （连跌后单日需涨 ≥11.5% 才能金叉，超出主板涨停），2024 至今 79 只全缓存池 0 命中。
- v2：参数不变（跌幅 6% / 阳线 / 量比 1.2 / MA5 上穿 MA10），仅把下跌与金叉在时间上解耦：
  近 15 日高点→低点回撤 ≥6% 且低点在最近 10 日内，当日金叉确认。
- 股票池由 10 只蓝筹改为全缓存池（79 只），避免手挑池的幸存者偏差。
- 预先约定只跑一次、不针对闸门调参。结果 wr=46.0% < 48%，**未过闸**。
- 生产扫描器 `_detect_bottom_reversal` 原为独立的 3 选 2 打分实现，已改为调用同一检测器（G-bt-7）。
