# value_investing

- assumptions: immediate=next_open*1.002; pullback=5d_low<=entry; breakout=5d_high>=entry*1.002; stop=daily_low then close; hard_exit=evaluate_hard_exit; hold<=10d
- n=3254 wr=47.5% pf=1.269 dd=-951.672
- oos_n=406 oos_wr=44.3% expired=0
- passed=False

- [OK] G-bt-2: n=3254 need>=200
- [NO] G-bt-3: wr=47.5% need>=55%
- [OK] G-bt-4: pf=1.269 need>=1.2
- [NO] G-bt-5: dd=-951.672 need<=25.0
- [OK] G-bt-6: oos_n=406 oos_wr=44.3% floor=39.5%
- [OK] G-bt-7: detectors imported from stock_analyzer.detectors
