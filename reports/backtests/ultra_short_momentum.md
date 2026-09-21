# ultra_short_momentum

- assumptions: immediate=next_open*1.002; pullback=5d_low<=entry; breakout=5d_high>=entry*1.002; stop=daily_low then close; hard_exit=evaluate_hard_exit; hold<=10d
- n=443 wr=45.8% pf=0.947 dd=-228.574
- oos_n=102 oos_wr=52.0% expired=0
- passed=False

- [OK] G-bt-2: n=443 need>=200
- [NO] G-bt-3: wr=45.8% need>=48%
- [NO] G-bt-4: pf=0.947 need>=1.2
- [NO] G-bt-5: dd=-228.574 need<=15.0
- [OK] G-bt-6: oos_n=102 oos_wr=52.0% floor=37.8%
- [OK] G-bt-7: detectors imported from stock_analyzer.detectors
