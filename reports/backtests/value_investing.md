# value_investing

- assumptions: immediate=next_open*1.002; pullback=5d_low<=entry; breakout=5d_high>=entry*1.002; stop=daily_low then close; hard_exit=evaluate_hard_exit; hold<=10d
- n=619 wr=45.9% pf=1.027 dd=-32.234
- oos_n=107 oos_wr=39.2% expired=0
- passed=False

- [OK] G-bt-2: n=619 need>=200
- [NO] G-bt-3: wr=45.9% need>=55%
- [NO] G-bt-4: pf=1.027 need>=1.2
- [NO] G-bt-5: dd=-32.234 need<=25.0
- [OK] G-bt-6: oos_n=107 oos_wr=39.2% floor=37.9%
- [OK] G-bt-7: detectors imported from stock_analyzer.detectors
