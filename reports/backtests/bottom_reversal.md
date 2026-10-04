# bottom_reversal

- assumptions: immediate=next_open*1.002; pullback=5d_low<=entry; breakout=5d_high>=entry*1.002; stop=daily_low then close; hard_exit=evaluate_hard_exit; hold<=10d
- n=0 wr=0.0% pf=0.0 dd=0.0
- oos_n=0 oos_wr=0.0% expired=0
- passed=False

- [NO] G-bt-2: n=0 need>=200
- [NO] G-bt-3: wr=0.0% need>=48%
- [NO] G-bt-4: pf=0.0 need>=1.2
- [OK] G-bt-5: dd=0.0 need<=15.0
- [OK] G-bt-6: oos_n=0 oos_wr=0.0% floor=-8.0%
- [OK] G-bt-7: detectors imported from stock_analyzer.detectors
