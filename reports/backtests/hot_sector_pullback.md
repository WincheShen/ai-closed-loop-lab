# hot_sector_pullback

- assumptions: immediate=next_open*1.002; pullback=5d_low<=entry; breakout=5d_high>=entry*1.002; stop=daily_low then close; hard_exit=evaluate_hard_exit; hold<=10d
- n=310 wr=40.3% pf=1.095 dd=-18.241
- oos_n=87 oos_wr=41.4% expired=13
- passed=False

- [OK] G-bt-2: n=310 need>=200
- [NO] G-bt-3: wr=40.3% need>=48%
- [NO] G-bt-4: pf=1.095 need>=1.2
- [NO] G-bt-5: dd=-18.241 need<=15.0
- [OK] G-bt-6: oos_n=87 oos_wr=41.4% floor=32.3%
- [OK] G-bt-7: detectors imported from stock_analyzer.detectors
